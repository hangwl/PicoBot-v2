import 'dart:async';
import 'package:flutter/foundation.dart';
import '../services/websocket_service.dart';
import '../services/storage_service.dart';
import '../models/key_config.dart';

/// Provider for managing WebSocket connection state
class ConnectionProvider extends ChangeNotifier {
  final WebSocketService _wsService = WebSocketService();
  final StorageService _storageService;

  bool _isConnected = false;
  String _serverHost = '192.168.1.100';
  int _serverPort = 8765;
  bool _autoConnect = true;
  bool _isMacroPlaying = false;
  String? _lastError;
  bool _isShiftPressed = false;
  int _shiftPressCount = 0; // number of active SHIFT presses (supports multiple shift keys)
  final Set<String> _pressedKeys = {};
  bool _isLoading = true;

  ConnectionProvider(this._storageService) {
    _init();
  }

  // Getters
  bool get isConnected => _isConnected;
  String get serverHost => _serverHost;
  int get serverPort => _serverPort;
  bool get autoConnect => _autoConnect;
  bool get isMacroPlaying => _isMacroPlaying;
  String? get lastError => _lastError;
  bool get isShiftPressed => _isShiftPressed;
  bool isKeyPressed(String keyId) => _pressedKeys.contains(keyId);
  bool get isLoading => _isLoading;
  String get serverAddress => '$_serverHost:$_serverPort';

  /// Initialize provider
  Future<void> _init() async {
    // Load settings
    _serverHost = await _storageService.getServerHost();
    _serverPort = await _storageService.getServerPort();
    _autoConnect = await _storageService.getAutoConnect();

    // Set up WebSocket callbacks
    _wsService.onConnectionChanged = (connected) {
      _isConnected = connected;
      if (!connected) {
        _isMacroPlaying = false;
      }
      notifyListeners();
    };

    _wsService.onMessageReceived = (message) {
      _handleMessage(message);
    };

    _wsService.onError = (error) {
      _lastError = error;
      notifyListeners();
    };

    // Auto-connect if enabled
    if (_autoConnect) {
      connect();
    }

    _isLoading = false;
    notifyListeners();
  }

  /// Handle incoming WebSocket messages
  void _handleMessage(String message) {
    if (message == 'macro|playing') {
      _isMacroPlaying = true;
      notifyListeners();
    } else if (message == 'macro|stopped') {
      _isMacroPlaying = false;
      notifyListeners();
    }
  }

  /// Connect to server
  Future<void> connect() async {
    _lastError = null;
    await _wsService.connect(_serverHost, _serverPort);
  }

  /// Disconnect from server
  void disconnect() {
    _wsService.disconnect();
  }

  /// Update server settings
  Future<void> updateServerSettings(String host, int port, bool autoConnect) async {
    _serverHost = host;
    _serverPort = port;
    _autoConnect = autoConnect;

    await _storageService.setServerHost(host);
    await _storageService.setServerPort(port);
    await _storageService.setAutoConnect(autoConnect);

    notifyListeners();
  }

  /// Handles the logic for a key being pressed down.
  void handleKeyPress(KeyConfig keyConfig) {
    // Update shift state if a shift key is pressed
    if (keyConfig.keyCode == 'shift') {
      _shiftPressCount++;
      _isShiftPressed = _shiftPressCount > 0;
    }
    _pressedKeys.add(keyConfig.id);
    notifyListeners();

    // Send the actual key/mouse command
    if (_isConnected) {
      if (keyConfig.type == 'mouse') {
        _wsService.sendMouseDown(keyConfig.keyCode);
      } else {
        _wsService.sendKeyDown(keyConfig.keyCode);
      }
    }
  }

  /// Handles the logic for a key being released.
  void handleKeyRelease(KeyConfig keyConfig) {
    // Update shift state if a shift key is released
    if (keyConfig.keyCode == 'shift') {
      if (_shiftPressCount > 0) _shiftPressCount--;
      _isShiftPressed = _shiftPressCount > 0;
    }
    _pressedKeys.remove(keyConfig.id);
    notifyListeners();

    // Send the actual key/mouse command
    if (_isConnected) {
      if (keyConfig.type == 'mouse') {
        _wsService.sendMouseUp(keyConfig.keyCode);
      } else {
        _wsService.sendKeyUp(keyConfig.keyCode);
      }
    }
  }

  /// Start macro
  void startMacro() {
    _wsService.startMacro();
  }

  /// Stop macro
  void stopMacro() {
    _wsService.stopMacro();
  }

  @override
  void dispose() {
    _wsService.dispose();
    super.dispose();
  }
}
