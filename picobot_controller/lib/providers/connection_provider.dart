import 'package:flutter/foundation.dart';
import '../services/websocket_service.dart';
import '../services/storage_service.dart';

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

  /// Send key down
  void sendKeyDown(String key) {
    _wsService.sendKeyDown(key);
  }

  /// Send key up
  void sendKeyUp(String key) {
    _wsService.sendKeyUp(key);
  }

  /// Send mouse down
  void sendMouseDown(String button) {
    _wsService.sendMouseDown(button);
  }

  /// Send mouse up
  void sendMouseUp(String button) {
    _wsService.sendMouseUp(button);
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
