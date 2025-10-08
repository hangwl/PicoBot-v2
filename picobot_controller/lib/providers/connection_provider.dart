import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import '../services/websocket_service.dart';
import '../services/storage_service.dart';
import '../models/key_config.dart';
import '../models/server_profile.dart';

/// Provider for managing WebSocket connection state
class ConnectionProvider extends ChangeNotifier {
  final WebSocketService _wsService = WebSocketService();
  final StorageService _storageService;

  bool _isConnected = false;
  // Server profiles
  List<ServerProfile> _profiles = [];
  String? _selectedProfileId;
  bool _isMacroPlaying = false;
  String? _lastError;
  bool _isShiftPressed = false;
  int _shiftPressCount = 0; // number of active SHIFT presses (supports multiple shift keys)
  final Set<String> _pressedKeys = {};
  bool _isLoading = true;
  Duration? _lastPingRtt;
  List<String> _playlists = [];
  String? _selectedPlaylist;

  ConnectionProvider(this._storageService) {
    _init();
  }

  // Getters
  bool get isConnected => _isConnected;
  // Legacy getters (computed from selected profile when present)
  String get serverHost => selectedProfile?.host ?? '—';
  int get serverPort => selectedProfile?.port ?? 0;
  bool get autoConnect => _selectedProfileId != null; // deprecated
  bool get isMacroPlaying => _isMacroPlaying;
  String? get lastError => _lastError;
  bool get isShiftPressed => _isShiftPressed;
  bool isKeyPressed(String keyId) => _pressedKeys.contains(keyId);
  bool get isLoading => _isLoading;
  String get serverAddress =>
      selectedProfile != null ? '${selectedProfile!.host}:${selectedProfile!.port}' : '—';
  List<ServerProfile> get profiles => List.unmodifiable(_profiles);
  ServerProfile? get selectedProfile =>
      _profiles.where((p) => p.id == _selectedProfileId).cast<ServerProfile?>().firstWhere((e) => true, orElse: () => null);
  Duration? get lastPingRtt => _lastPingRtt;
  List<String> get playlists => List.unmodifiable(_playlists);
  String? get selectedPlaylist => _selectedPlaylist;

  /// Initialize provider
  Future<void> _init() async {
    // Profiles: migrate legacy and load
    await _storageService.migrateLegacyServerSettingsIfNeeded();
    _profiles = await _storageService.getServerProfiles();
    _selectedProfileId = await _storageService.getSelectedServerProfileId();

    // Set up WebSocket callbacks
    _wsService.onConnectionChanged = (connected) {
      _isConnected = connected;
      if (connected) {
        requestPlaylists();
      } else {
        _isMacroPlaying = false;
        _playlists = [];
        _selectedPlaylist = null;
        _lastPingRtt = null;
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

    _wsService.onPingRtt = (rtt) {
      _lastPingRtt = rtt;
      notifyListeners();
    };

    // Auto-connect if a selected profile exists
    if (_selectedProfileId != null && selectedProfile != null) {
      connect();
    }

    _isLoading = false;
    notifyListeners();
  }

  /// Handle incoming WebSocket messages
  void _handleMessage(String message) {
    if (message.startsWith('{')) {
      try {
        final data = jsonDecode(message);
        if (data['event'] == 'macroPlaylists') {
          _playlists = List<String>.from(data['playlists'] ?? []);
          // If the current selection is no longer valid, clear it
          if (_selectedPlaylist != null && !_playlists.contains(_selectedPlaylist)) {
            _selectedPlaylist = null;
          }
          notifyListeners();
        }
      } catch (e) {
        // Not a valid JSON message, ignore
      }
      return;
    }

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
    final profile = selectedProfile;
    if (profile == null) {
      notifyListeners();
      return;
    }
    await _wsService.connect(profile.host, profile.port);
  }

  /// Disconnect from server
  void disconnect() {
    _wsService.disconnect();
    _lastPingRtt = null;
  }

  // ========== Profiles API ==========
  Future<void> reloadProfiles() async {
    _profiles = await _storageService.getServerProfiles();
    notifyListeners();
  }

  Future<void> selectProfile(String id) async {
    _selectedProfileId = id;
    await _storageService.setSelectedServerProfileId(id);
    notifyListeners();
    await connect();
  }

  Future<void> clearSelectedProfile() async {
    _selectedProfileId = null;
    await _storageService.setSelectedServerProfileId(null);
    disconnect();
    notifyListeners();
  }

  Future<void> addOrUpdateProfile(ServerProfile profile) async {
    final idx = _profiles.indexWhere((p) => p.id == profile.id);
    if (idx >= 0) {
      _profiles[idx] = profile.copyWith(updatedAt: DateTime.now());
    } else {
      _profiles.add(profile);
    }
    await _storageService.saveServerProfiles(_profiles);
    notifyListeners();
    if (_selectedProfileId == profile.id) {
      await connect();
    }
  }

  Future<void> deleteProfile(String id) async {
    _profiles.removeWhere((p) => p.id == id);
    await _storageService.saveServerProfiles(_profiles);
    if (_selectedProfileId == id) {
      await clearSelectedProfile();
    } else {
      notifyListeners();
    }
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

  // ========== Macro Playlists API ==========

  /// Request the list of macro playlists from the server.
  void requestPlaylists() {
    _wsService.send('macro|playlists|get');
  }

  /// Set the active macro playlist on the server.
  void selectPlaylist(String? playlist) {
    _selectedPlaylist = playlist;
    if (playlist != null) {
      _wsService.send('macro|playlists|set|$playlist');
    } else {
      _wsService.send('macro|playlists|set|');
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _wsService.dispose();
    super.dispose();
  }
}