#pragma once
#include "axi_master.hh"
#include "aou_backend.hh"
#include "axi_monitor.hh"

// The only connection between the CoralNPU AXI master and the shared storage.
namespace storage_axi {
class Demo : public sc_core::sc_module {
 public:
    SC_HAS_PROCESS(Demo);
    Demo(sc_core::sc_module_name, const ConfigParams&);
    ~Demo();
    bool done() const { return master.done; }
    uint64_t cycles() const { return master.cycles(); }
    uint32_t mailbox() const { return master.mailbox(); }
    uint64_t doneTick() const { return master.doneTick; }
    void finish();
 private:
    Signals wires;
    sc_core::sc_clock clock;
    sc_core::sc_signal<bool> resetn{"resetn"};
    Master master;
    AouBackend storage;
    AxiMonitor monitor;
    sc_core::sc_trace_file* wave = nullptr;
    std::string directory;
    bool finished = false;
    void reset();
};
}
