#pragma once
#include "axi_master.hh"
#include "config.hh"
#include <map>

namespace storage_axi {
class AouBackend;
class Demo : public sc_core::sc_module {
  public:
    SC_HAS_PROCESS(Demo);
    Demo(sc_core::sc_module_name, const ConfigParams&);
    bool done() const { return master.done; }
    uint64_t cycles() const { return master.cycles(); }
    uint64_t doneTick() const { return master.doneTick; }
    uint32_t mailbox() const { return master.mailbox(); }
    void finish();
    ~Demo();
  private:
    Signals wires;
    sc_core::sc_clock clock;
    sc_core::sc_signal<bool> resetn{"resetn"};
    Master master;
    std::unique_ptr<AouBackend> aou;
    sc_core::sc_trace_file* vcd = nullptr;
    std::ofstream events;
    std::string directory;
    uint64_t cycle = 0;
    bool finished = false;
    std::map<std::string, std::vector<Data>> held;
    std::map<std::string, uint64_t> handshakes, stalled;
    void reset();
    void sample();
    void channel(const std::string&, bool valid, bool ready,
                 const std::vector<Data>& payload);
    void row(const char*, uint64_t id = 0, uint64_t addr = 0,
             unsigned len = 0, unsigned size = 0, Data data = 0,
             unsigned strb = 0, bool last = false, unsigned resp = 0);
};
}
