import 'dart:async';
import 'dart:math';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as status;
import '../utils/constants.dart';
import 'logger_service.dart';

/// WebSocket service for communicating with PicoBot server
class WebSocketService {
  // Singleton factory
  static final WebSocketService _instance = WebSocketService._internal();
  factory WebSocketService() => _instance;
  WebSocketService._internal();
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  Timer? _reconnectTimer;
  Timer? _pingTimer;
  bool _awaitingHeartbeat = false; // true if last ping has not received any message yet
  int _reconnectAttempts = 0; // grows with each consecutive failure
  final Random _random = Random();
  // Ping/pong tracking
  String? _lastPingNonce;
  DateTime? _lastPingSentAt;
  int _pingSeq = 0;

  String _host = ConnectionDefaults.defaultHost;
  int _port = ConnectionDefaults.defaultPort;
  bool _isConnecting = false;
  bool _shouldReconnect = true;

  /// Connection state callbacks
  Function(bool connected)? onConnectionChanged;
  Function(String message)? onMessageReceived;
  Function(String error)? onError;
  // Optional: report ping RTT to observers
  Function(Duration rtt)? onPingRtt;

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
      LoggerService().dF('WS', () => 'Connecting to $uri');
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
      LoggerService().i('WS', 'Connected to ws://$_host:$_port');

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

    // Handle dedicated pong first; do not forward to app callbacks
    if (msg.startsWith('pong|')) {
      final parts = msg.split('|');
      if (parts.length >= 2) {
        final nonce = parts[1];
        if (_lastPingNonce != null && nonce == _lastPingNonce) {
          _awaitingHeartbeat = false;
          if (_lastPingSentAt != null) {
            final rtt = DateTime.now().difference(_lastPingSentAt!);
            onPingRtt?.call(rtt);
            LoggerService().dF('WS', () => 'Pong $nonce; rtt=${rtt.inMilliseconds}ms');
          }
          // Clear last ping markers
          _lastPingNonce = null;
          _lastPingSentAt = null;
        }
      }
      return; // swallow pong
    }

    // Backward-compat: any non-pong message counts as heartbeat
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
    LoggerService().e('WS', error);
  }

  /// Handle disconnection
  void _handleDisconnect() {
    _cleanup();
    onConnectionChanged?.call(false);
    LoggerService().w('WS', 'Disconnected');
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
    LoggerService().wF('WS', () => 'Reconnect in ${delay.inMilliseconds}ms (attempt ${_reconnectAttempts + 1})');

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
          _sendPing();
        } catch (e) {
          // Connection lost, will be handled by stream listener
        }
      } else {
        timer.cancel();
      }
    });
  }

  void _sendPing() {
    _awaitingHeartbeat = true;
    _lastPingNonce = 'n${_pingSeq++}-${DateTime.now().millisecondsSinceEpoch}';
    _lastPingSentAt = DateTime.now();
    send('ping|$_lastPingNonce');
    LoggerService().dF('WS', () => 'Ping $_lastPingNonce');
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
    // On web, only 1000 or 3000-4999 are allowed for application close codes.
    // Use normalClosure (1000) to avoid DOM exceptions.
    _channel?.sink.close(status.normalClosure);
    _channel = null;
    _isConnecting = false;
  }

  /// Dispose service
  void dispose() {
    disconnect();
  }
}
