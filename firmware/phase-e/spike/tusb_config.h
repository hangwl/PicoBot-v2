#ifndef TUSB_CONFIG_H_
#define TUSB_CONFIG_H_

#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE)
#define CFG_TUSB_OS           OPT_OS_NONE

#define CFG_TUD_ENDPOINT0_SIZE 64

#define CFG_TUD_HID           2
#define CFG_TUD_HID_EP_BUFSIZE 64
#define CFG_TUD_CDC           0
#define CFG_TUD_MSC           0
#define CFG_TUD_MIDI          0
#define CFG_TUD_VENDOR        0

#endif
