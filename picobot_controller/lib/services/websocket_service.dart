import 'dart:async';
import 'dart:math';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as status;
import '../utils/constants.dart';

/// WebSocket service for communicating with PicoBot server
class WebSocketService {
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;
  Timer? _pingTimer;
  bool _awaitingHeartbeat = false; // true if last ping has not received any message yet
  int _reconnectAttempts = 0; // grows with each consecutive failure
  final Random _random = Random();

  String _host = ConnectionDefaults.defaultHost;
  int _port = ConnectionDefaults.defaultPort;
  bool _isConnecting = false;
  bool _shouldReconnect = true;

  /// Connection state callbacks
  Function(bool connected)? onConnectionChanged;
  Function(String message)? onMessageReceived;
  Function(String error)? onError;

  /// Current connection state
  bool get isConnected => _channel != null;

  /// Get current server address
  String get serverAddress => '$_host:$_port';

  /// Connect to WebSocket server
  Future<void> connect(String host, int port) async {
    if (_isConnecting) return;

    _host = host;
    _port = port;
    _shouldReconnect = true;
    await _connect();
  }

  /// Internal connection logic
  Future<void> _connect() async {
    if (_isConnecting || _channel != null) return;

    _isConnecting = true;
    try {
      final uri = Uri.parse('ws://$_host:$_port');
      _channel = WebSocketChannel.connect(uri);

      // Listen to messages
      _subscription = _channel!.stream.listen(
        (message) {
          _handleMessage(message.toString());
        },
        onError: (error) {
          // Treat errors as a disconnect: cleanup, notify, and schedule reconnect.
          _handleError('WebSocket error: $error');
          _handleDisconnect();
        },
        onDone: () {
          _handleDisconnect();
        },
        cancelOnError: true,
      );

      // Reset heartbeat state and start ping timer to keep connection alive
      _awaitingHeartbeat = false;
      _startPingTimer();

      onConnectionChanged?.call(true);
      // Successful connection resets backoff attempts
      _reconnectAttempts = 0;
      _isConnecting = false;

      // Query initial macro state
      send('macro|query');
    } catch (e) {
      _isConnecting = false;
      _handleError('Connection failed: $e');
      _reconnect();
    }
  }

  /// Handle incoming messages
  void _handleMessage(String message) {
    final msg = message.trim();
    if (msg.isEmpty) return;

    // Any message counts as heartbeat reply
    _awaitingHeartbeat = false;

    // Notify listeners
    onMessageReceived?.call(msg);

    // Handle specific messages
    if (msg == 'macro|playing' || msg == 'macro|stopped') {
      // Macro state updates handled by listeners
    }
  }

  /// Handle connection errors
  void _handleError(String error) {
    onError?.call(error);
  }

  /// Handle disconnection
  void _handleDisconnect() {
    _cleanup();
    onConnectionChanged?.call(false);
    if (_shouldReconnect) {
      _reconnect();
    }
  }

  /// Attempt to reconnect
  void _reconnect() {
    _reconnectTimer?.cancel();
    // Full jitter exponential backoff: random(0, min(cap, base * 2^attempt))
    final int baseMs = ConnectionDefaults.reconnectBaseDelay.inMilliseconds;
    final int capMs = ConnectionDefaults.reconnectMaxDelay.inMilliseconds;
    // Compute exponential growth, capping to avoid overflow
    int expFactor;
    try {
      expFactor = 1 << _reconnectAttempts; // 2^attempts
    } catch (_) {
      expFactor = 1 << 30; // fallback large value
    }
    int ceiling = baseMs * expFactor;
    if (ceiling > capMs) ceiling = capMs;
    if (ceiling < baseMs) ceiling = baseMs; // guard

    final int jitterMs = _random.nextInt(ceiling + 1); // [0, ceiling]
    final delay = Duration(milliseconds: jitterMs);

    _reconnectTimer = Timer(delay, () {
      if (_shouldReconnect) {
        _connect();
      }
    });

    // Prepare for the next attempt
    if (_reconnectAttempts < 30) {
      _reconnectAttempts += 1;
    }
  }

  /// Start ping timer to keep connection alive
  void _startPingTimer() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(ConnectionDefaults.pingInterval, (timer) {
      if (_channel != null) {
        // If previous ping had no message since then, assume connection is stale
        if (_awaitingHeartbeat) {
          _handleDisconnect();
          return;
        }
        try {
          _awaitingHeartbeat = true;
          // Send a lightweight query to keep connection alive
          send('macro|query');
        } catch (e) {
          // Connection lost, will be handled by stream listener
        }
      } else {
        timer.cancel();
      }
    });
  }

  /// Send a message to the server
  void send(String message) {
    if (_channel == null) {
      _handleError('Not connected');
      return;
    }

    try {
      _channel!.sink.add(message);
    } catch (e) {
      _handleError('Send failed: $e');
      _reconnect();
    }
  }

  /// Send key press command
  void sendKeyDown(String key) {
    send('key|down|$key');
  }

  /// Send key release command
  void sendKeyUp(String key) {
    send('key|up|$key');
  }

  /// Send mouse button press
  void sendMouseDown(String button) {
    send('mouse|down|$button');
  }

  /// Send mouse button release
  void sendMouseUp(String button) {
    send('mouse|up|$button');
  }

  /// Send mouse movement
  void sendMouseMove(int x, int y) {
    send('hid|move|$x|$y');
  }

  /// Start macro playback
  void startMacro() {
    send('macro|start');
  }

  /// Stop macro playback
  void stopMacro() {
    send('macro|stop');
  }

  /// Query macro status
  void queryMacroStatus() {
    send('macro|query');
  }

  /// Disconnect from server
  void disconnect() {
    _shouldReconnect = false;
    _cleanup();
    onConnectionChanged?.call(false);
    // Manual disconnect should clear backoff
    _reconnectAttempts = 0;
  }

  /// Clean up resources
  void _cleanup() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _awaitingHeartbeat = false;
    _subscription?.cancel();
    _subscription = null;
    _channel?.sink.close(status.goingAway);
    _channel = null;
    _isConnecting = false;
  }

  /// Dispose service
  void dispose() {
    disconnect();
  }
}
