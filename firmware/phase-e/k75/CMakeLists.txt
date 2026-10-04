cmake_minimum_required(VERSION 3.13)
set(PICO_BOARD pico_w)
if (EXISTS ${CMAKE_CURRENT_LIST_DIR}/pico_sdk_import.cmake)
    include(pico_sdk_import.cmake)
else ()
    include($ENV{PICO_SDK_PATH}/external/pico_sdk_import.cmake)
endif ()
project(k75 C CXX ASM)
pico_sdk_init()

add_executable(k75 main.c keymap.c usb_descriptors.c)
target_include_directories(k75 PRIVATE ${CMAKE_CURRENT_LIST_DIR})
# Command handling nests a few hundred bytes of buffers inside the USB
# callback chain; the default 2K stack is tight.
target_compile_definitions(k75 PRIVATE PICO_STACK_SIZE=0x1000)
target_link_libraries(k75 pico_stdlib tinyusb_device hardware_watchdog)
pico_add_extra_outputs(k75)
