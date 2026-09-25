#pragma once
#include "axi_signals.hh"
#include "config.hh"
#include "coralnpu_native.h"
#include <array>
#include <deque>
#include <fstream>
#include <map>
#include <memory>
namespace storage_axi {
class Master : public sc_core::sc_module {
 public:
    sc_core::sc_in<bool> clk{"clk"}, resetn{"resetn"};
    MasterPorts axi;
    SC_HAS_PROCESS(Master);
    Master(sc_core::sc_module_name,const ConfigParams&);
    ~Master();
    bool idle() const {return active.empty();}
    bool done=false;
    uint64_t accepted=0, completed=0, maxActive=0, capacityDenials=0, cycle=0, doneTick=0;
    uint64_t cycles() const {return coralnpu_cycles(device);}
    uint32_t mailbox() const {return coralnpu_mailbox(device);}
 private:
    struct Txn {
        uint64_t seq, address, begin, accept, axiDone=0;
        uint16_t id, mask;
        uint8_t nativeId, response=0;
        bool write, ready=false;
        std::array<uint8_t,16> data{};
        uint64_t awAfter, wAfter;
    };
    ConfigParams config;
    coralnpu_handle device=nullptr;
    sc_core::sc_clock nativeClock;
    uint16_t nextId=1;
    std::map<uint16_t,Txn> active;
    std::map<std::pair<bool,uint8_t>,std::deque<uint16_t>> order;
    std::deque<uint16_t> awq,wq,arq;
    std::ofstream trace,source;
    void nativeTick();
    void tick();
    void drive();
    void submit(uint64_t,uint32_t,uint8_t,bool,const uint8_t*,uint16_t);
    void sourceRow(const char*,const Txn&);
    void retire();
};
}
