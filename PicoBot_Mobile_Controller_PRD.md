# PicoBot Mobile Controller - Product Requirements Document

**Version:** 1.0  
**Last Updated:** 2025-10-06  
**Platform:** Android (Flutter)  
**Status:** In Development

---

## 1. Executive Summary

PicoBot Mobile Controller is a Flutter-based Android application that provides a customizable, touch-optimized remote keyboard/mouse interface for controlling the PicoBot macro system. The app enables users to create, customize, and use keyboard templates with drag-and-drop positioning, supporting split-screen and picture-in-picture (PiP) modes for flexible multitasking.

---

## 2. Product Overview

### 2.1 Problem Statement
The current web-based PicoBot remote controller (`index.html`) has limitations:
- Fixed button layouts not optimized for different screen sizes
- No customization for user-specific key bindings
- Poor mobile UX for drag-and-drop interactions
- Limited support for Android multitasking features (split-screen, PiP)

### 2.2 Solution
A native Android app built with Flutter that provides:
- **Customizable keyboard templates** with drag-and-drop key positioning
- **Responsive layouts** that adapt to window size changes
- **Multi-window support** (split-screen, PiP)
- **Template management** (create, save, edit, delete)
- **WebSocket integration** with existing PicoBot Python server

### 2.3 Target Users
- PicoBot users who want mobile remote control
- Users who need customized key layouts for specific games/applications
- Users who want to multitask while controlling PicoBot (split-screen, PiP)

---

## 3. Core Features

### 3.1 Template Management
**Priority:** P0 (Must Have)

#### Requirements
- **Create Template:** Users can create new keyboard templates from scratch
- **Edit Template:** Modify existing templates (add/remove/reposition keys)
- **Delete Template:** Remove unwanted templates
- **Duplicate Template:** Clone existing templates as starting points
- **Template List:** View all saved templates with preview thumbnails
- **Default Template:** Ship with 1-2 pre-configured templates

#### Acceptance Criteria
- [ ] User can create a new blank template
- [ ] User can name/rename templates
- [ ] Templates persist across app restarts
- [ ] User can delete templates with confirmation dialog
- [ ] At least one default template is available on first launch

---

### 3.2 Key Selection & Customization
**Priority:** P0 (Must Have)

#### Requirements
- **Key Menu:** Scrollable list of available keys to add
  - Alphabet keys (A-Z)
  - Number keys (0-9)
  - Special keys (Space, Enter, Shift, Ctrl, Alt, Esc, Tab, Backspace)
  - Arrow keys (Up, Down, Left, Right)
  - Function keys (F1-F12)
  - Mouse buttons (Left Click, Right Click)
  - Custom keys (user-defined labels)

- **Add Key:** Tap key from menu to add to canvas
- **Remove Key:** Long-press or swipe to delete key from canvas
- **Key Properties:**
  - Label (display text)
  - Key code (actual key to send)
  - Size (width, height)
  - Position (x%, y% of screen)

#### Acceptance Criteria
- [ ] User can browse and search available keys
- [ ] User can add keys to template canvas
- [ ] User can remove keys from canvas
- [ ] Keys display correct labels
- [ ] Minimum 50+ keys available in menu

---

### 3.3 Drag-and-Drop Interface
**Priority:** P0 (Must Have)

#### Requirements
- **Drag Keys:** Touch and drag keys to reposition on canvas
- **Resize Keys:** Pinch or drag corners to resize keys
- **Snap to Grid:** Optional grid snapping for alignment (toggle on/off)
- **Visual Feedback:** Show drag shadow/outline during movement
- **Collision Detection:** Prevent keys from overlapping (optional)
- **Undo/Redo:** Support undo/redo for positioning changes

#### Acceptance Criteria
- [ ] Keys can be dragged smoothly with touch
- [ ] Keys can be resized with pinch gesture or corner handles
- [ ] Visual feedback shows during drag operations
- [ ] Position changes are saved to template
- [ ] Grid snapping works when enabled

---

### 3.4 Lock/Unlock Mode
**Priority:** P0 (Must Have)

#### Requirements
- **Edit Mode (Unlocked):** Keys are draggable and resizable
- **Use Mode (Locked):** Keys are fixed, send commands on press
- **Mode Toggle:** Clear button/switch to toggle between modes
- **Visual Indicator:** Different appearance for locked vs unlocked state

#### Acceptance Criteria
- [ ] User can toggle between edit and use modes
- [ ] In edit mode, keys can be repositioned
- [ ] In use mode, keys send WebSocket commands
- [ ] Mode state is visually distinct
- [ ] Mode persists per template

---

### 3.5 WebSocket Communication
**Priority:** P0 (Must Have)

#### Requirements
- **Connection Setup:** User enters server IP and port
- **Auto-connect:** Remember last connection, auto-reconnect on app start
- **Connection Status:** Visual indicator (connected/disconnected)
- **Command Protocol:** Support existing PicoBot protocol
  - `key|down|<key>` - Key press
  - `key|up|<key>` - Key release
  - `mouse|down|left` - Mouse press
  - `mouse|up|left` - Mouse release
  - `macro|start` - Start macro
  - `macro|stop` - Stop macro
  - `macro|query` - Query macro status

#### Acceptance Criteria
- [ ] User can configure server IP and port
- [ ] App connects to WebSocket server
- [ ] Connection status is displayed
- [ ] Key presses send correct protocol messages
- [ ] App handles disconnection gracefully
- [ ] Auto-reconnect works after network loss

---

### 3.6 Responsive Layout & Multi-Window Support
**Priority:** P1 (Should Have)

#### Requirements
- **Percentage-based Positioning:** Store key positions as % of screen size
- **Dynamic Recalculation:** Recalculate positions on window size change
- **Split-Screen Support:** Layout adapts when app is in split-screen mode
- **PiP Support:** Simplified layout for picture-in-picture mode
- **Orientation Support:** Handle portrait and landscape orientations
- **Multi-Layout Templates:** Templates can define layouts for different screen sizes
  - Fullscreen layout (>600dp width)
  - Split-screen layout (300-600dp width)
  - PiP layout (<300dp width)

#### Acceptance Criteria
- [ ] Keys maintain relative positions in split-screen
- [ ] PiP mode shows simplified layout (4-6 essential keys)
- [ ] App handles orientation changes smoothly
- [ ] Touch targets meet minimum size (48dp) in all modes
- [ ] Template editor is disabled in PiP mode

---

### 3.7 Template Storage
**Priority:** P0 (Must Have)

#### Requirements
- **Local Storage:** Templates saved as JSON files
- **Template Schema:**
```json
{
  "id": "uuid",
  "name": "Template Name",
  "version": "1.0",
  "created_at": "2025-10-06T21:25:00Z",
  "updated_at": "2025-10-06T21:25:00Z",
  "layouts": {
    "fullscreen": {
      "min_width": 600,
      "keys": [
        {
          "id": "key_uuid",
          "label": "W",
          "key_code": "w",
          "type": "key",
          "x_percent": 0.25,
          "y_percent": 0.40,
          "width_percent": 0.15,
          "height_percent": 0.10
        }
      ]
    },
    "split": {
      "min_width": 300,
      "max_width": 599,
      "keys": [/* compact layout */]
    },
    "pip": {
      "max_width": 299,
      "keys": [/* minimal layout */]
    }
  }
}
```

#### Acceptance Criteria
- [ ] Templates are saved to local storage
- [ ] Templates persist across app restarts
- [ ] JSON schema is validated on load
- [ ] Corrupted templates are handled gracefully
- [ ] Export/import templates (future enhancement)

---

## 4. Technical Architecture

### 4.1 Technology Stack
- **Framework:** Flutter 3.x
- **Language:** Dart
- **State Management:** Provider or Riverpod
- **Storage:** shared_preferences + JSON files
- **WebSocket:** web_socket_channel package
- **UI Components:** Material Design 3

### 4.2 Key Packages
```yaml
dependencies:
  flutter:
    sdk: flutter
  web_socket_channel: ^2.4.0
  shared_preferences: ^2.2.0
  provider: ^6.0.5
  uuid: ^4.0.0
  flutter_draggable_gridview: ^0.1.3
  pip_view: ^0.0.2
```

### 4.3 Project Structure
```
lib/
├── main.dart
├── models/
│   ├── template.dart
│   ├── key_config.dart
│   └── layout_config.dart
├── services/
│   ├── websocket_service.dart
│   ├── template_service.dart
│   └── storage_service.dart
├── providers/
│   ├── template_provider.dart
│   ├── connection_provider.dart
│   └── layout_provider.dart
├── screens/
│   ├── home_screen.dart
│   ├── template_list_screen.dart
│   ├── template_editor_screen.dart
│   ├── controller_screen.dart
│   └── settings_screen.dart
├── widgets/
│   ├── draggable_key_button.dart
│   ├── key_menu.dart
│   ├── connection_status.dart
│   └── template_card.dart
└── utils/
    ├── constants.dart
    └── helpers.dart
```

### 4.4 Data Flow
1. **Template Creation:** User creates template → Saved to local storage
2. **Template Loading:** App loads templates → Displays in list
3. **Template Editing:** User edits template → Updates in memory → Saves to storage
4. **Controller Mode:** User locks template → Keys send WebSocket commands
5. **Layout Adaptation:** Window size changes → Recalculate key positions → Re-render

---

## 5. User Interface

### 5.1 Screen Flow
```
Splash Screen
    ↓
Home Screen (Template List)
    ├── → Template Editor (Create/Edit)
    │       ├── Key Menu (Add Keys)
    │       ├── Canvas (Drag/Resize)
    │       └── Save/Lock
    ├── → Controller Screen (Use Template)
    │       └── WebSocket Commands
    └── → Settings Screen
            ├── Server Configuration
            └── App Preferences
```

### 5.2 Key Screens

#### Home Screen
- Template list with thumbnails
- "Create New Template" FAB
- Connection status indicator
- Settings icon

#### Template Editor
- Canvas area (main workspace)
- Key menu (bottom sheet or side panel)
- Lock/Unlock toggle
- Save button
- Delete template option

#### Controller Screen
- Full-screen key layout
- Connection status
- Macro start/stop button
- Back to template list

#### Settings Screen
- Server IP input
- Server port input
- Auto-connect toggle
- Grid snap toggle
- About/version info

---

## 6. Development Phases

### Phase 1: MVP (Weeks 1-2)
**Goal:** Basic functionality with single template

- [x] Project setup (Flutter, dependencies)
- [x] Project structure (models, services, providers, screens, widgets, utils)
- [x] Data models (KeyConfig, LayoutConfig, Template)
- [x] Constants (available keys, breakpoints, defaults)
- [x] JSON serialization setup
- [ ] Basic UI structure (Home, Editor, Controller screens)
- [ ] Template model and storage (single template)
- [ ] Key menu with basic keys (A-Z, 0-9, Space, Enter)
- [ ] Drag-and-drop on canvas (absolute positioning)
- [ ] Lock/unlock mode toggle
- [ ] WebSocket connection (hardcoded server)
- [ ] Send key press/release commands

### Phase 2: Template Management (Week 3)
**Goal:** Multiple templates with CRUD operations

- [ ] Template list screen
- [ ] Create/delete templates
- [ ] Template naming
- [ ] Template persistence (multiple files)
- [ ] Default templates

### Phase 3: Advanced Editing (Week 4)
**Goal:** Enhanced editor features

- [ ] Key resizing (pinch/corner handles)
- [ ] Grid snapping
- [ ] Undo/redo
- [ ] Key properties editor
- [ ] Collision detection
- [ ] Template duplication

### Phase 4: Responsive Layouts (Week 5)
**Goal:** Multi-window support

- [ ] Percentage-based positioning
- [ ] Window size detection
- [ ] Split-screen adaptation
- [ ] PiP mode implementation
- [ ] Multi-layout templates
- [ ] Orientation handling

### Phase 5: Polish & Testing (Week 6)
**Goal:** Production-ready app

- [ ] UI/UX refinements
- [ ] Error handling
- [ ] Connection retry logic
- [ ] Performance optimization
- [ ] Testing (unit, widget, integration)
- [ ] Documentation

---

## 7. Success Metrics

### 7.1 Functional Metrics
- [ ] User can create and save templates
- [ ] User can customize key positions and sizes
- [ ] WebSocket commands are sent correctly
- [ ] App works in split-screen mode
- [ ] App works in PiP mode
- [ ] Templates persist across app restarts

### 7.2 Performance Metrics
- WebSocket latency: <50ms
- Key press response time: <100ms
- Template load time: <500ms
- Smooth 60fps dragging

### 7.3 Quality Metrics
- Zero crashes in normal operation
- Graceful handling of network errors
- Accessible touch targets (min 48dp)
- Intuitive UX (minimal learning curve)

---

## 8. Future Enhancements (Post-MVP)

### 8.1 Advanced Features
- [ ] Template sharing (export/import JSON)
- [ ] Cloud sync (Firebase/Supabase)
- [ ] Gesture support (swipe, long-press macros)
- [ ] Haptic feedback
- [ ] Custom themes/skins
- [ ] Macro recording from mobile
- [ ] Voice commands integration

### 8.2 Platform Expansion
- [ ] iOS version
- [ ] Tablet optimization (larger layouts)
- [ ] Wear OS companion app
- [ ] Web version (PWA)

---

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| WebSocket connection instability | High | Implement auto-reconnect, offline mode |
| Complex drag-and-drop on mobile | Medium | Use proven Flutter packages, extensive testing |
| PiP mode limitations (Android) | Low | Provide clear UX for PiP constraints |
| Template storage corruption | Medium | JSON validation, backup/restore feature |
| Performance on low-end devices | Medium | Optimize rendering, limit max keys per template |

---

## 10. Open Questions

- [ ] Should we support Bluetooth connection as fallback?
- [ ] Maximum number of keys per template?
- [ ] Should templates be shareable between users?
- [ ] Support for custom key icons/images?
- [ ] Multi-touch gestures (e.g., two-finger swipe)?

---

## 11. Appendix

### 11.1 Existing PicoBot Protocol Reference
```
WebSocket Commands (Client → Server):
- key|down|<key>      # Press key
- key|up|<key>        # Release key
- mouse|down|left     # Mouse button down
- mouse|up|left       # Mouse button up
- hid|move|<x>|<y>    # Mouse movement
- macro|start         # Start macro playback
- macro|stop          # Stop macro playback
- macro|query         # Query macro status

WebSocket Responses (Server → Client):
- macro|playing       # Macro is running
- macro|stopped       # Macro is stopped
```

### 11.2 Supported Keys
```
Letters: a-z
Numbers: 0-9
Special: space, enter, shift, ctrl, alt, esc, tab, backspace
Arrows: up, down, left, right
Function: f1-f12
Symbols: -, =, [, ], \, ;, ', `, ,, ., /
Mouse: left, right, middle
```

### 11.3 Design References
- Material Design 3 Guidelines
- Android PiP Best Practices
- Flutter Draggable Widgets Examples

---

**Document Control:**
- **Owner:** PicoBot Development Team
- **Reviewers:** TBD
- **Next Review:** After Phase 1 completion
