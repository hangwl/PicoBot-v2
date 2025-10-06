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
        
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8.0),
          child: Tooltip(
            message: isConnected ? 'Connected' : 'Disconnected',
            child: Icon(
              Icons.circle,
              color: isConnected ? Colors.green : Colors.red,
              size: 16,
            ),
          ),
        );
      },
    );
  }
}
