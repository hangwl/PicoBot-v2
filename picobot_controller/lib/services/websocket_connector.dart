// lib/services/websocket_connector.dart

// Conditional import for platform-specific WebSocket implementations.
import 'websocket_connector_io.dart' if (dart.library.html) 'websocket_connector_web.dart';

/// Abstract base class for creating a WebSocket channel.
/// This allows for platform-specific implementations (IO vs. Web).
abstract class WebSocketConnector {
  /// Factory constructor to return the platform-specific instance.
  factory WebSocketConnector() => getWebSocketConnector();

  /// Connects to the WebSocket server at the given [uri].
  dynamic connect(Uri uri);
}
