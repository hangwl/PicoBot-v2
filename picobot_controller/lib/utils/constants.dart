/// Available keys that can be added to templates
class AvailableKeys {
  /// Letter keys
  static const List<Map<String, String>> letters = [
    {'label': 'a', 'keyCode': 'a', 'shiftLabel': 'A'},
    {'label': 'b', 'keyCode': 'b', 'shiftLabel': 'B'},
    {'label': 'c', 'keyCode': 'c', 'shiftLabel': 'C'},
    {'label': 'd', 'keyCode': 'd', 'shiftLabel': 'D'},
    {'label': 'e', 'keyCode': 'e', 'shiftLabel': 'E'},
    {'label': 'f', 'keyCode': 'f', 'shiftLabel': 'F'},
    {'label': 'g', 'keyCode': 'g', 'shiftLabel': 'G'},
    {'label': 'h', 'keyCode': 'h', 'shiftLabel': 'H'},
    {'label': 'i', 'keyCode': 'i', 'shiftLabel': 'I'},
    {'label': 'j', 'keyCode': 'j', 'shiftLabel': 'J'},
    {'label': 'k', 'keyCode': 'k', 'shiftLabel': 'K'},
    {'label': 'l', 'keyCode': 'l', 'shiftLabel': 'L'},
    {'label': 'm', 'keyCode': 'm', 'shiftLabel': 'M'},
    {'label': 'n', 'keyCode': 'n', 'shiftLabel': 'N'},
    {'label': 'o', 'keyCode': 'o', 'shiftLabel': 'O'},
    {'label': 'p', 'keyCode': 'p', 'shiftLabel': 'P'},
    {'label': 'q', 'keyCode': 'q', 'shiftLabel': 'Q'},
    {'label': 'r', 'keyCode': 'r', 'shiftLabel': 'R'},
    {'label': 's', 'keyCode': 's', 'shiftLabel': 'S'},
    {'label': 't', 'keyCode': 't', 'shiftLabel': 'T'},
    {'label': 'u', 'keyCode': 'u', 'shiftLabel': 'U'},
    {'label': 'v', 'keyCode': 'v', 'shiftLabel': 'V'},
    {'label': 'w', 'keyCode': 'w', 'shiftLabel': 'W'},
    {'label': 'x', 'keyCode': 'x', 'shiftLabel': 'X'},
    {'label': 'y', 'keyCode': 'y', 'shiftLabel': 'Y'},
    {'label': 'z', 'keyCode': 'z', 'shiftLabel': 'Z'},
  ];

  /// Number keys
  static const List<Map<String, String>> numbers = [
    {'label': '0', 'keyCode': '0', 'shiftLabel': ')'},
    {'label': '1', 'keyCode': '1', 'shiftLabel': '!'},
    {'label': '2', 'keyCode': '2', 'shiftLabel': '@'},
    {'label': '3', 'keyCode': '3', 'shiftLabel': '#'},
    {'label': '4', 'keyCode': '4', 'shiftLabel': r'$'},
    {'label': '5', 'keyCode': '5', 'shiftLabel': '%'},
    {'label': '6', 'keyCode': '6', 'shiftLabel': '^'},
    {'label': '7', 'keyCode': '7', 'shiftLabel': '&'},
    {'label': '8', 'keyCode': '8', 'shiftLabel': '*'},
    {'label': '9', 'keyCode': '9', 'shiftLabel': '('},
  ];

  /// Special keys
  static const List<Map<String, String>> special = [
    {'label': 'SPACE', 'keyCode': 'space'},
    {'label': 'ENTER', 'keyCode': 'enter'},
    {'label': 'SHIFT', 'keyCode': 'shift'},
    {'label': 'CTRL', 'keyCode': 'ctrl'},
    {'label': 'ALT', 'keyCode': 'alt'},
    {'label': 'ESC', 'keyCode': 'esc'},
    {'label': 'TAB', 'keyCode': 'tab'},
    {'label': 'BACKSPACE', 'keyCode': 'backspace'},
  ];

  /// Arrow keys
  static const List<Map<String, String>> arrows = [
    {'label': '↑', 'keyCode': 'up'},
    {'label': '↓', 'keyCode': 'down'},
    {'label': '←', 'keyCode': 'left'},
    {'label': '→', 'keyCode': 'right'},
  ];

  /// Function keys
  static const List<Map<String, String>> function = [
    {'label': 'F1', 'keyCode': 'f1'},
    {'label': 'F2', 'keyCode': 'f2'},
    {'label': 'F3', 'keyCode': 'f3'},
    {'label': 'F4', 'keyCode': 'f4'},
    {'label': 'F5', 'keyCode': 'f5'},
    {'label': 'F6', 'keyCode': 'f6'},
    {'label': 'F7', 'keyCode': 'f7'},
    {'label': 'F8', 'keyCode': 'f8'},
    {'label': 'F9', 'keyCode': 'f9'},
    {'label': 'F10', 'keyCode': 'f10'},
    {'label': 'F11', 'keyCode': 'f11'},
    {'label': 'F12', 'keyCode': 'f12'},
  ];

  /// Symbol keys
  static const List<Map<String, String>> symbols = [
    {'label': '-', 'keyCode': '-', 'shiftLabel': '_'},
    {'label': '=', 'keyCode': '=', 'shiftLabel': '+'},
    {'label': '[', 'keyCode': '[', 'shiftLabel': '{'},
    {'label': ']', 'keyCode': ']', 'shiftLabel': '}'},
    {'label': '\\', 'keyCode': '\\', 'shiftLabel': '|'},
    {'label': ';', 'keyCode': ';', 'shiftLabel': ':'},
    {'label': "'", 'keyCode': "'", 'shiftLabel': '"'},
    {'label': '`', 'keyCode': '`', 'shiftLabel': '~'},
    {'label': ',', 'keyCode': ',', 'shiftLabel': '<'},
    {'label': '.', 'keyCode': '.', 'shiftLabel': '>'},
    {'label': '/', 'keyCode': '/', 'shiftLabel': '?'},
  ];

  /// Mouse buttons
  static const List<Map<String, String>> mouse = [
    {'label': 'LMB', 'keyCode': 'left', 'type': 'mouse'},
    {'label': 'RMB', 'keyCode': 'right', 'type': 'mouse'},
    {'label': 'MMB', 'keyCode': 'middle', 'type': 'mouse'},
  ];

  /// Get all keys grouped by category
  static Map<String, List<Map<String, String>>> getAllKeysGrouped() {
    return {
      'Letters': letters,
      'Numbers': numbers,
      'Special': special,
      'Arrows': arrows,
      'Function': function,
      'Symbols': symbols,
      'Mouse': mouse,
    };
  }

  /// Get all keys as a flat list
  static List<Map<String, String>> getAllKeys() {
    return [
      ...letters,
      ...numbers,
      ...special,
      ...arrows,
      ...function,
      ...symbols,
      ...mouse,
    ];
  }
}

/// Default screen size breakpoints
class ScreenBreakpoints {
  static const double pipMaxWidth = 299.0;
  static const double splitMinWidth = 300.0;
  static const double splitMaxWidth = 599.0;
  static const double fullscreenMinWidth = 600.0;
}

/// The minimum size (width and height) for a key in logical pixels.
const double minKeySize = 64.0;

/// Default key sizes as percentages
class DefaultKeySizes {
  // Default size is now based on minKeySize, but defined in pixels.
  // The conversion to percentage will happen in the widget that adds the key.
  static const double width = minKeySize;
  static const double height = minKeySize;
  static const double minTouchTarget = 48.0; // Minimum touch target in dp
}

/// WebSocket connection defaults
class ConnectionDefaults {
  static const String defaultHost = '192.168.1.100';
  static const int defaultPort = 8765;
  static const Duration reconnectDelay = Duration(seconds: 2);
  static const Duration pingInterval = Duration(seconds: 20);
}