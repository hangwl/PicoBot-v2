/// Available keys that can be added to templates
class AvailableKeys {
  /// Letter keys
  static const List<Map<String, String>> letters = [
    {'label': 'A', 'keyCode': 'a'},
    {'label': 'B', 'keyCode': 'b'},
    {'label': 'C', 'keyCode': 'c'},
    {'label': 'D', 'keyCode': 'd'},
    {'label': 'E', 'keyCode': 'e'},
    {'label': 'F', 'keyCode': 'f'},
    {'label': 'G', 'keyCode': 'g'},
    {'label': 'H', 'keyCode': 'h'},
    {'label': 'I', 'keyCode': 'i'},
    {'label': 'J', 'keyCode': 'j'},
    {'label': 'K', 'keyCode': 'k'},
    {'label': 'L', 'keyCode': 'l'},
    {'label': 'M', 'keyCode': 'm'},
    {'label': 'N', 'keyCode': 'n'},
    {'label': 'O', 'keyCode': 'o'},
    {'label': 'P', 'keyCode': 'p'},
    {'label': 'Q', 'keyCode': 'q'},
    {'label': 'R', 'keyCode': 'r'},
    {'label': 'S', 'keyCode': 's'},
    {'label': 'T', 'keyCode': 't'},
    {'label': 'U', 'keyCode': 'u'},
    {'label': 'V', 'keyCode': 'v'},
    {'label': 'W', 'keyCode': 'w'},
    {'label': 'X', 'keyCode': 'x'},
    {'label': 'Y', 'keyCode': 'y'},
    {'label': 'Z', 'keyCode': 'z'},
  ];

  /// Number keys
  static const List<Map<String, String>> numbers = [
    {'label': '0', 'keyCode': '0'},
    {'label': '1', 'keyCode': '1'},
    {'label': '2', 'keyCode': '2'},
    {'label': '3', 'keyCode': '3'},
    {'label': '4', 'keyCode': '4'},
    {'label': '5', 'keyCode': '5'},
    {'label': '6', 'keyCode': '6'},
    {'label': '7', 'keyCode': '7'},
    {'label': '8', 'keyCode': '8'},
    {'label': '9', 'keyCode': '9'},
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
    {'label': '-', 'keyCode': '-'},
    {'label': '=', 'keyCode': '='},
    {'label': '[', 'keyCode': '['},
    {'label': ']', 'keyCode': ']'},
    {'label': '\\', 'keyCode': '\\'},
    {'label': ';', 'keyCode': ';'},
    {'label': "'", 'keyCode': "'"},
    {'label': '`', 'keyCode': '`'},
    {'label': ',', 'keyCode': ','},
    {'label': '.', 'keyCode': '.'},
    {'label': '/', 'keyCode': '/'},
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

/// Default key sizes as percentages
class DefaultKeySizes {
  static const double width = 0.15; // 15% of screen width
  static const double height = 0.10; // 10% of screen height
  static const double minTouchTarget = 48.0; // Minimum touch target in dp
}

/// WebSocket connection defaults
class ConnectionDefaults {
  static const String defaultHost = '192.168.1.100';
  static const int defaultPort = 8765;
  static const Duration reconnectDelay = Duration(seconds: 2);
  static const Duration pingInterval = Duration(seconds: 20);
}
