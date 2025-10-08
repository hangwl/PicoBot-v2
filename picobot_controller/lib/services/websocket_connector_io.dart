// lib/services/websocket_connector_io.dart

import 'dart:io';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'websocket_connector.dart';

/// IO implementation of WebSocketConnector.
class WebSocketConnectorIO implements WebSocketConnector {
  @override
  Future<WebSocketChannel> connect(Uri uri) async {
    // For IO, we can disable compression.
    final ws = await WebSocket.connect(
      uri.toString(),
      compression: CompressionOptions(enabled: false),
    );
    return IOWebSocketChannel(ws);
  }
}

WebSocketConnector getWebSocketConnector() => WebSocketConnectorIO();
