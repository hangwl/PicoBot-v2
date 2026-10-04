// Identity of the Sonix K75 (no serial number, 1 ms polling). The spike has
// two HID interfaces: 0 = boot keyboard, 1 = vendor-defined 64-byte channel.
#include "tusb.h"

#define USB_VID 0x0C45
#define USB_PID 0x8006

enum { ITF_KEYBOARD = 0, ITF_VENDOR, ITF_COUNT };

static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0111,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x00,
    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *)&desc_device; }

static uint8_t const desc_hid_keyboard[] = {TUD_HID_REPORT_DESC_KEYBOARD()};
static uint8_t const desc_hid_vendor[] = {TUD_HID_REPORT_DESC_GENERIC_INOUT(64)};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    return instance == ITF_KEYBOARD ? desc_hid_keyboard : desc_hid_vendor;
}

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_HID_DESC_LEN + TUD_HID_INOUT_DESC_LEN)
#define EPNUM_KBD_IN 0x81
#define EPNUM_VENDOR_OUT 0x02
#define EPNUM_VENDOR_IN 0x82

static uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_COUNT, 0, CONFIG_TOTAL_LEN, TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_HID_DESCRIPTOR(ITF_KEYBOARD, 0, HID_ITF_PROTOCOL_KEYBOARD, sizeof(desc_hid_keyboard),
                       EPNUM_KBD_IN, 16, 1),
    TUD_HID_INOUT_DESCRIPTOR(ITF_VENDOR, 0, HID_ITF_PROTOCOL_NONE, sizeof(desc_hid_vendor),
                             EPNUM_VENDOR_OUT, EPNUM_VENDOR_IN, 64, 1),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_configuration;
}

static char const *string_desc[] = {
    (const char[]){0x09, 0x04}, // 0: English (US)
    "SONIX",                    // 1: manufacturer
    "K75",                      // 2: product
};

static uint16_t desc_str[32];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    uint8_t chr_count;
    if (index == 0) {
        memcpy(&desc_str[1], string_desc[0], 2);
        chr_count = 1;
    } else {
        if (index >= sizeof(string_desc) / sizeof(string_desc[0])) return NULL;
        const char *str = string_desc[index];
        chr_count = (uint8_t)strlen(str);
        if (chr_count > 31) chr_count = 31;
        for (uint8_t i = 0; i < chr_count; i++) desc_str[1 + i] = str[i];
    }
    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
    return desc_str;
}
