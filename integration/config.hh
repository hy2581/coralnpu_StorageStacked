#pragma once
#include "storage_config.hh"
namespace storage_axi {
struct ConfigParams : StorageConfig {
    uint64_t period=2000000, device_period=2000000, max_ticks=20000000000000;
    unsigned outstanding=16;
    bool stalls=true;
    std::string kernel;
};
}
