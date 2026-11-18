#pragma once

#include <stdint.h>

struct Settings {
  char room[12];
  char ssid[33];
  char pass[65];
  char host[64];
  uint16_t port;
  char key[65];
  char tried[40];
};

struct Press {
  uint8_t kind;
  uint16_t seq;
  uint32_t born;
};

struct Picture {
  int32_t rev;
  uint16_t bar;
  uint16_t murIcon;
  uint16_t laundryIcon;
  uint16_t bell;
  uint16_t dndBtn;
  uint16_t murBtn;
  uint16_t laundryBtn;
};

struct Net {
  char room[12];
  char host[64];
  uint16_t port;
  char key[65];
};
