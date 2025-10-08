import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/connection_provider.dart';

/// Widget showing WebSocket connection status
class ConnectionStatusWidget extends StatelessWidget {
  const ConnectionStatusWidget({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<ConnectionProvider>(
      builder: (context, connectionProvider, child) {
        final isConnected = connectionProvider.isConnected;
        final isConnecting = connectionProvider.isConnecting && !isConnected;
        final color = isConnected
            ? Colors.green
            : (isConnecting ? Colors.orange : Colors.red);
        final label = isConnected
            ? 'Connected'
            : (isConnecting ? 'Reconnecting…' : 'Disconnected');
        
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8.0),
          child: Tooltip(
            message: label,
            child: Icon(Icons.circle, color: color, size: 16),
          ),
        );
      },
    );
  }
}
