#include "storage.hh"
namespace storage_axi {
using namespace sc_core;
Demo::Demo(sc_module_name name, const ConfigParams& p)
    : sc_module(name), clock("aclk", sc_time::from_value(p.period)),
      master("master", p), storage("aou", p), monitor("monitor", wires, p.trace_dir),
      directory(p.trace_dir) {
    master.clk(clock); master.resetn(resetn); master.axi.bind(wires);
    storage.clk(clock); storage.resetn(resetn); storage.axi.bind(wires);
    monitor.clk(clock); monitor.resetn(resetn);
    wave = sc_create_vcd_trace_file((directory + "/axi_wave").c_str());
    wave->set_time_unit(1, SC_FS);
    sc_trace(wave, clock, "ACLK"); sc_trace(wave, resetn, "ARESETn");
    wires.trace(wave); storage.trace(wave);
    SC_THREAD(reset);
}
Demo::~Demo() { if (wave) sc_close_vcd_trace_file(wave); }
void Demo::reset() {
    resetn = false;
    wait(clock.period() * 3 + clock.period() / 2);
    while (!storage.ready()) wait(clock.period());
    resetn = true;
}
void Demo::finish() {
    if (finished) return;
    storage.finish(directory);
    AxiMasterStats stats{master.accepted, master.completed, master.maxActive,
                         master.capacityDenials, master.idle()};
    monitor.finish(clock.period().value(), &stats);
    sc_close_vcd_trace_file(wave); wave = nullptr;
    finished = true;
}
}
