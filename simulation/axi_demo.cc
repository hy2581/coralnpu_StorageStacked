#include "axi_demo.hh"
#include "aou_backend.hh"
#include <stdexcept>

namespace storage_axi {
using namespace sc_core;
Demo::Demo(sc_module_name n, const ConfigParams& p)
    : sc_module(n),
      clock("aclk", sc_time::from_value(p.period)),
      master("master", p),
      events(p.trace_dir + "/axi_events.csv"), directory(p.trace_dir) {
    if (!events) throw std::runtime_error("cannot open AXI trace");
    master.clk(clock); master.resetn(resetn); master.axi.bind(wires);
    if (p.memory_backend != "memsim")
        throw std::invalid_argument("This project requires the online AXI/UCIe/mem_sim path");
    if (p.outstanding > 1023) throw std::runtime_error("AoU supports at most 1023 live IDs");
    aou = std::make_unique<AouBackend>("aou", p);
    aou->clk(clock); aou->resetn(resetn); aou->axi.bind(wires);
    events << "tick,cycle,channel,id,address,len,size,data,strb,last,resp\n";
    vcd = sc_create_vcd_trace_file((directory + "/axi_wave").c_str());
    vcd->set_time_unit(1, SC_FS);
    sc_trace(vcd, clock, "ACLK"); sc_trace(vcd, resetn, "ARESETn");
    wires.trace(vcd);
    if (aou) aou->trace(vcd);
    SC_THREAD(reset);
    SC_METHOD(sample); sensitive << clock.posedge_event(); dont_initialize();
}
Demo::~Demo() = default;
void Demo::reset() {
    resetn = false;
    wait(clock.period() * 3 + clock.period() / 2);
    while (aou && !aou->ready()) wait(clock.period());
    resetn = true;
}
void Demo::channel(const std::string& n, bool valid, bool ready,
                   const std::vector<Data>& payload) {
    auto it = held.find(n);
    if (it != held.end()) {
        if (!valid || payload != it->second)
            SC_REPORT_FATAL("AXI stability", n.c_str());
    }
    if (valid && !ready) { held[n] = payload; ++stalled[n]; }
    else held.erase(n);
    if (valid && ready) ++handshakes[n];
}
void Demo::row(const char* n, uint64_t id, uint64_t a, unsigned len,
               unsigned size, Data d, unsigned strb, bool last, unsigned resp) {
    events << sc_time_stamp().value() << ',' << cycle << ',' << n << ',' << id << ','
           << a << ',' << len << ',' << size << ',' << d.to_string(sc_dt::SC_DEC, false) << ',' << strb << ','
           << last << ',' << resp << '\n';
}
void Demo::sample() {
    ++cycle;
    if (!resetn.read()) return;
    auto& w = wires;
    channel("AW", w.awvalid, w.awready, {Data(w.awid.read()), Data(w.awaddr.read()), Data(w.awlen.read()), Data(w.awsize.read()), Data(w.awburst.read())});
    channel("W", w.wvalid, w.wready, {w.wdata.read(), Data(w.wstrb.read()), Data(w.wlast.read())});
    channel("B", w.bvalid, w.bready, {Data(w.bid.read()), Data(w.bresp.read())});
    channel("AR", w.arvalid, w.arready, {Data(w.arid.read()), Data(w.araddr.read()), Data(w.arlen.read()), Data(w.arsize.read()), Data(w.arburst.read())});
    channel("R", w.rvalid, w.rready, {Data(w.rid.read()), w.rdata.read(), Data(w.rresp.read()), Data(w.rlast.read())});
    if (w.awvalid && w.awready) row("AW", w.awid.read(), w.awaddr.read(), w.awlen.read(), w.awsize.read());
    if (w.wvalid && w.wready) row("W", 0, 0, 0, 0, w.wdata.read(), w.wstrb.read(), w.wlast.read());
    if (w.bvalid && w.bready) row("B", w.bid.read(), 0, 0, 0, 0, 0, false, w.bresp.read());
    if (w.arvalid && w.arready) row("AR", w.arid.read(), w.araddr.read(), w.arlen.read(), w.arsize.read());
    if (w.rvalid && w.rready) row("R", w.rid.read(), 0, 0, 0, w.rdata.read(), 0, w.rlast.read(), w.rresp.read());
    events.flush();
}
void Demo::finish() {
    if (finished) return;
    finished = true;
    events.flush();
    if (aou) aou->finish(directory);
    if (vcd) {
        sc_close_vcd_trace_file(vcd);
        vcd = nullptr;
    }
    std::ofstream f(directory + "/protocol_summary.json");
    f << "{\"accepted\":" << master.accepted << ",\"completed\":" << master.completed
      << ",\"max_outstanding\":" << master.maxActive << ",\"capacity_denials\":" << master.capacityDenials << ",\"drained\":"
      << (master.idle() ? "true" : "false") << ",\"ticks_per_second\":"
      << 1000000000000000ull << ",\"period_ticks\":" << clock.period().value()
      << ",\"axi_data_bits\":" << DataBits << ",\"channels\":{";
    bool first = true;
    for (auto n : {"AW", "W", "B", "AR", "R"}) {
        if (!first) f << ',';
        first = false;
        f << '"' << n << "\":{\"handshakes\":" << handshakes[n]
          << ",\"stall_cycles\":" << stalled[n] << '}';
    }
    f << "}}\n";
}
}
