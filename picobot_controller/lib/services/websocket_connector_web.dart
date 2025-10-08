// lib/services/websocket_connector_web.dart

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/html.dart';
import 'websocket_connector.dart';

/// Web implementation of WebSocketConnector.
class WebSocketConnectorWeb implements WebSocketConnector {
  @override
  WebSocketChannel connect(Uri uri) {
    // For web, we use HtmlWebSocketChannel, which doesn't support disabling compression.
    return HtmlWebSocketChannel.connect(uri);
  }
}

WebSocketConnector getWebSocketConnector() => WebSocketConnectorWeb();
