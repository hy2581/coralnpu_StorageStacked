#include "axi_master.hh"
#include <algorithm>
#include <iomanip>
#include <stdexcept>
namespace storage_axi {
using namespace sc_core;
Master::Master(sc_module_name name,const ConfigParams& p):sc_module(name),config(p),
 nativeClock("npu_clock",sc_time::from_value(p.device_period)),
 trace(p.trace_dir+"/transactions.csv"),source(p.trace_dir+"/npu_requests.csv") {
    if(!trace || !source) throw std::runtime_error("Cannot open native request logs");
    trace<<"id,command,address,bytes,begin_tick,accepted_tick,axi_done_tick,end_resp_tick,segments,requestor,stream,substream,status\n";
    source<<"event,tick,sequence,command,address,native_id,wire_id,data,mask,response\n";
    device=coralnpu_create([](void* p,uint64_t seq,uint32_t addr,uint8_t id,int write,const uint8_t* data,uint16_t mask){
        static_cast<Master*>(p)->submit(seq,addr,id,write,data,mask);
    },[](void* p){auto& self=*static_cast<Master*>(p);bool ready=self.active.size()<self.config.outstanding;
        if(!ready) ++self.capacityDenials;return int(ready);},this);
    if(!device || coralnpu_load(device,p.kernel.c_str())) throw std::runtime_error("Cannot load CoralNPU ELF");
    SC_METHOD(tick);sensitive<<clk.pos();dont_initialize();
    SC_METHOD(nativeTick);sensitive<<nativeClock.posedge_event();dont_initialize();
}
Master::~Master(){coralnpu_destroy(device);}
void Master::sourceRow(const char* event,const Txn& t) {
    source<<event<<','<<sc_time_stamp().value()<<','<<t.seq<<','<<(t.write?'W':'R')<<','
          <<t.address<<','<<unsigned(t.nativeId)<<','<<t.id<<',';
    if(t.write || t.ready) for(auto byte:t.data) source<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(byte);
    source<<std::dec<<','<<t.mask<<','<<unsigned(t.response)<<'\n';source.flush();
}
void Master::submit(uint64_t seq,uint32_t addr,uint8_t nativeId,bool write,const uint8_t* data,uint16_t mask) {
    if(active.size()>=config.outstanding || (addr&15)) throw std::runtime_error("Native request exceeds adapter capacity/alignment");
    while(active.count(nextId)) nextId=nextId==1023?1:nextId+1;
    auto id=nextId;nextId=nextId==1023?1:nextId+1;
    Txn t{};t.seq=seq;t.address=addr;t.id=id;t.mask=mask;t.nativeId=nativeId;t.write=write;
    t.begin=t.accept=sc_time_stamp().value();
    if(write) std::copy(data,data+16,t.data.begin());
    t.awAfter=cycle+(config.stalls && (id&1)?3:0);
    t.wAfter=cycle+(config.stalls && !(id&1)?3:0);
    active.emplace(id,t);order[{write,nativeId}].push_back(id);
    if(write){awq.push_back(id);wq.push_back(id);}else arq.push_back(id);
    ++accepted;maxActive=std::max<uint64_t>(maxActive,active.size());sourceRow("request",t);
}
void Master::retire() {
    for(auto& [key,ids]:order) while(!ids.empty() && active.at(ids.front()).ready) {
        auto t=active.at(ids.front());
        coralnpu_complete(device,t.seq,t.nativeId,t.write,t.data.data(),t.response);
        sourceRow("response",t);
        trace<<t.id<<','<<(t.write?'W':'R')<<','<<t.address<<",16,"<<t.begin<<','<<t.accept<<','
             <<t.axiDone<<','<<sc_time_stamp().value()<<",1,0,"<<unsigned(t.nativeId)<<','<<t.seq<<','<<(t.response?2:1)<<'\n';
        active.erase(t.id);ids.pop_front();++completed;
    }
    trace.flush();
}
void Master::nativeTick() {
    if(!resetn.read() || done) return;
    retire();
    if(!coralnpu_step(device)) {
        if(!idle()) throw std::runtime_error("NPU stopped with incomplete external accesses");
        done=true;doneTick=sc_time_stamp().value();
        std::cout<<"CoralNPU kernel finished after "<<cycles()<<" cycles, mailbox="<<std::hex<<mailbox()<<std::dec<<'\n';
    }
}
void Master::tick() {
    ++cycle;
    if(!resetn.read()) {axi.awvalid=false;axi.wvalid=false;axi.arvalid=false;axi.rready=false;axi.bready=false;return;}
    if(axi.awvalid.read() && axi.awready.read()){sc_assert(!awq.empty());awq.pop_front();}
    if(axi.wvalid.read() && axi.wready.read()){sc_assert(!wq.empty());wq.pop_front();}
    if(axi.arvalid.read() && axi.arready.read()){sc_assert(!arq.empty());arq.pop_front();}
    if(axi.rvalid.read() && axi.rready.read()) {
        auto& t=active.at(axi.rid.read().to_uint());sc_assert(!t.write && !t.ready && axi.rlast.read());
        unsigned lane=t.address%32;auto data=axi.rdata.read();
        for(unsigned j=0;j<16;++j)t.data[j]=data.range(8*(lane+j)+7,8*(lane+j)).to_uint();
        t.response=axi.rresp.read().to_uint();t.ready=true;t.axiDone=sc_time_stamp().value();
    }
    if(axi.bvalid.read() && axi.bready.read()) {
        auto& t=active.at(axi.bid.read().to_uint());sc_assert(t.write && !t.ready);
        t.response=axi.bresp.read().to_uint();t.ready=true;t.axiDone=sc_time_stamp().value();
    }
    drive();
}
void Master::drive() {
    axi.awvalid=false;
    if(!awq.empty()){const auto& t=active.at(awq.front());axi.awaddr=t.address;axi.awid=t.id;axi.awlen=0;axi.awsize=4;axi.awburst=1;axi.awvalid=cycle>=t.awAfter;}
    axi.arvalid=false;
    if(!arq.empty()){const auto& t=active.at(arq.front());axi.araddr=t.address;axi.arid=t.id;axi.arlen=0;axi.arsize=4;axi.arburst=1;axi.arvalid=true;}
    axi.wvalid=false;
    if(!wq.empty()){
        const auto& t=active.at(wq.front());Data data=0;unsigned lane=t.address%32;
        for(unsigned j=0;j<16;++j)data.range(8*(lane+j)+7,8*(lane+j))=t.data[j];
        axi.wdata=data;axi.wstrb=uint32_t(t.mask)<<lane;axi.wlast=true;axi.wvalid=cycle>=t.wAfter;
    }
    axi.rready=!config.stalls || cycle%7>=3;
    axi.bready=!config.stalls || cycle%5>=2;
}
}
