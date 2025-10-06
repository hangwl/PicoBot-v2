import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:picobot_controller/screens/manage_templates_screen.dart';
import '../providers/connection_provider.dart';

/// Settings screen for server configuration
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late TextEditingController _hostController;
  late TextEditingController _portController;
  bool _autoConnect = true;

  @override
  void initState() {
    super.initState();
    final connectionProvider = context.read<ConnectionProvider>();
    _hostController = TextEditingController(text: connectionProvider.serverHost);
    _portController = TextEditingController(text: connectionProvider.serverPort.toString());
    _autoConnect = connectionProvider.autoConnect;
  }

  @override
  void dispose() {
    _hostController.dispose();
    _portController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Server Configuration Section
          Text(
            'Server Configuration',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _hostController,
            decoration: const InputDecoration(
              labelText: 'Server Host',
              hintText: '192.168.1.100',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.computer),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _portController,
            decoration: const InputDecoration(
              labelText: 'Server Port',
              hintText: '8765',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.numbers),
            ),
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            title: const Text('Auto-connect on startup'),
            subtitle: const Text('Automatically connect when app starts'),
            value: _autoConnect,
            onChanged: (value) {
              setState(() {
                _autoConnect = value;
              });
            },
          ),
          const SizedBox(height: 24),
          Consumer<ConnectionProvider>(
            builder: (context, connectionProvider, child) {
              return Column(
                children: [
                  if (connectionProvider.isConnected)
                    ElevatedButton.icon(
                      onPressed: () {
                        connectionProvider.disconnect();
                      },
                      icon: const Icon(Icons.link_off),
                      label: const Text('Disconnect'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: Colors.red,
                        minimumSize: const Size(double.infinity, 48),
                      ),
                    )
                  else
                    ElevatedButton.icon(
                      onPressed: () async {
                        final host = _hostController.text.trim();
                        final port = int.tryParse(_portController.text.trim()) ?? 8765;
                        
                        await connectionProvider.updateServerSettings(
                          host,
                          port,
                          _autoConnect,
                        );
                        connectionProvider.connect();
                      },
                      icon: const Icon(Icons.link),
                      label: const Text('Connect'),
                      style: ElevatedButton.styleFrom(
                        minimumSize: const Size(double.infinity, 48),
                      ),
                    ),
                  const SizedBox(height: 16),
                  ElevatedButton.icon(
                    onPressed: () async {
                      final host = _hostController.text.trim();
                      final port = int.tryParse(_portController.text.trim()) ?? 8765;
                      
                      await connectionProvider.updateServerSettings(
                        host,
                        port,
                        _autoConnect,
                      );
                      
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Settings saved')),
                        );
                      }
                    },
                    icon: const Icon(Icons.save),
                    label: const Text('Save Settings'),
                    style: ElevatedButton.styleFrom(
                      minimumSize: const Size(double.infinity, 48),
                    ),
                  ),
                ],
              );
            },
          ),
          const SizedBox(height: 32),

          // Template Management Section
          Text(
            'Templates',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 16),
          ElevatedButton.icon(
            icon: const Icon(Icons.folder_copy_outlined),
            label: const Text('Manage Templates'),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const ManageTemplatesScreen()),
              );
            },
            style: ElevatedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48),
            ),
          ),

          const SizedBox(height: 32),
          // App Info Section
          Text(
            'About',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 16),
          const ListTile(
            leading: Icon(Icons.info),
            title: Text('Version'),
            subtitle: Text('1.0.0'),
          ),
          const ListTile(
            leading: Icon(Icons.description),
            title: Text('PicoBot Mobile Controller'),
            subtitle: Text('Customizable remote keyboard controller'),
          ),
        ],
      ),
    );
  }
}
