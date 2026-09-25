#include "axi_demo.hh"
#include <boost/property_tree/ptree.hpp>
#include <boost/property_tree/json_parser.hpp>
#include <fstream>
#include <iostream>
using namespace sc_core;
int sc_main(int argc,char** argv) {
    if(argc==2 && std::string(argv[1])=="--check") {std::cout<<"CoralNPU native / standalone SystemC / online mem_sim\n";return 0;}
    if(argc!=3) {std::cerr<<"usage: coralnpu_sim resolved.json output-directory\n";return 2;}
    const std::string directory=argv[2];
    try {
        sc_set_time_resolution(1,SC_FS);
        boost::property_tree::ptree c;boost::property_tree::read_json(argv[1],c);
        auto& a=c.get_child("architecture");storage_axi::ConfigParams p;
        p.trace_dir=directory;p.kernel=c.get<std::string>("kernel");
        p.period=a.get<uint64_t>("axi.period_ns")*1000000;
        p.device_period=1000000000/a.get<unsigned>("device_clock_mhz");
        p.max_ticks=a.get<uint64_t>("max_ticks");p.outstanding=a.get<unsigned>("axi.outstanding");
        p.planes=a.get<unsigned>("axi.planes");p.stalls=a.get<bool>("axi.stalls");p.replay=a.get<bool>("axi.replay");
        p.memsim_channels=a.get<unsigned>("memory.channels");p.memsim_scale=a.get<unsigned>("memory.scale");
        p.memsim_slots=a.get<unsigned>("memory.slots");p.memsim_queue=a.get<unsigned>("memory.queue");
        p.memsim_response_hold=a.get<unsigned>("memory.response_hold");p.memsim_standard=a.get<std::string>("memory.standard");
        // Record values read by the executable, independent of Python's resolved snapshot.
        std::ofstream config(directory+"/config.json");
        config<<"{\"runtime\":\"standalone-systemc\",\"device\":\"coralnpu\",\"device_period_fs\":"<<p.device_period
              <<",\"axi\":{\"base\":"<<p.base<<",\"size\":"<<p.size<<",\"period\":"<<p.period
              <<",\"outstanding\":"<<p.outstanding<<",\"planes\":"<<p.planes<<",\"replay\":"<<(p.replay?"true":"false")
              <<",\"stalls\":"<<(p.stalls?"true":"false")<<",\"memsim_slots\":"<<p.memsim_slots<<"}}\n";config.close();
        storage_axi::Demo demo("storage",p);
        while(!demo.done() && sc_time_stamp().value()<p.max_ticks)
            sc_start(sc_time::from_value(std::min<uint64_t>(100000000,p.max_ticks-sc_time_stamp().value())));
        bool passed=demo.done() && demo.mailbox()==0x600d0000;
        std::ofstream result(directory+"/completion.json");
        result<<"{\"passed\":"<<(passed?"true":"false")<<",\"code\":"<<(passed?0:1)
              <<",\"cause\":\""<<(demo.done()?"CoralNPU: kernel complete":"watchdog timeout")<<"\",\"tick_fs\":"
              <<(demo.done()?demo.doneTick():sc_time_stamp().value())<<",\"cycles\":"<<demo.cycles()<<",\"mailbox\":"<<demo.mailbox()<<"}\n";
        if(passed)demo.finish();return passed?0:1;
    } catch(const std::exception& e) {
        std::cerr<<"Native simulation failed: "<<e.what()<<'\n';
        std::ofstream(directory+"/completion.json")<<"{\"passed\":false,\"code\":1,\"cause\":\"simulation exception; see run.log\"}\n";
        return 1;
    }
}
