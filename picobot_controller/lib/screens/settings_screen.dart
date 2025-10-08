import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:provider/provider.dart';
import 'package:picobot_controller/screens/manage_templates_screen.dart';
import '../providers/connection_provider.dart';
import '../models/server_profile.dart';
import 'log_console_screen.dart';

/// Settings screen for server configuration
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Templates Section (moved to top)
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

          // Server Profiles Section
          Text(
            'Server Profiles',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 8),
          Consumer<ConnectionProvider>(
            builder: (context, connectionProvider, child) {
              final profiles = connectionProvider.profiles;
              final selected = connectionProvider.selectedProfile;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (profiles.isEmpty)
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Theme.of(context).colorScheme.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Text('No profiles yet. Add one to get started.'),
                    ),
                  ...profiles.map((p) => Card(
                        child: ListTile(
                          leading: Icon(
                            selected?.id == p.id
                                ? Icons.radio_button_checked
                                : Icons.radio_button_off,
                            color: selected?.id == p.id
                                ? Theme.of(context).colorScheme.primary
                                : null,
                          ),
                          title: Text(p.name),
                          subtitle: Text('${p.host}:${p.port}'),
                          onTap: () => connectionProvider.selectProfile(p.id),
                          trailing: Transform.translate(
                            offset: const Offset(8, 0), // Shift right to reduce trailing space
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                              IconButton(
                                style: IconButton.styleFrom(
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                  backgroundColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                                ),
                                tooltip: 'Edit',
                                icon: const Icon(Icons.edit, size: 20),
                                onPressed: () => _showProfileDialog(context, existing: p),
                              ),
                              const SizedBox(width: 8),
                              IconButton(
                                style: IconButton.styleFrom(
                                  shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(8),
                                  ),
                                  backgroundColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                                ),
                                tooltip: 'Delete',
                                icon: const Icon(Icons.delete, color: Colors.red, size: 20),
                                onPressed: () async {
                                  final confirmed = await showDialog<bool>(
                                    context: context,
                                    builder: (_) => AlertDialog(
                                      title: const Text('Delete profile?'),
                                      content: Text('Delete "${p.name}"?'),
                                      actions: [
                                        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
                                        TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Delete')),
                                      ],
                                    ),
                                  );
                                  if (confirmed == true) {
                                    await connectionProvider.deleteProfile(p.id);
                                  }
                                },
                              ),
                            ],
                          ),),
                        ),
                      )),
                  const SizedBox(height: 8),
                  // Row 1: Add Profile (full width)
                  Row(
                    children: [
                      Expanded(
                        child: ElevatedButton.icon(
                          icon: const Icon(Icons.add),
                          label: const Text('Add Profile'),
                          onPressed: () => _showProfileDialog(context),
                          style: ElevatedButton.styleFrom(
                            minimumSize: const Size(double.infinity, 48),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  // Row 2: Reconnect and Disconnect
                  Row(
                    children: [
                      Expanded(
                        child: ElevatedButton.icon(
                          icon: const Icon(Icons.refresh),
                          label: const Text('Reconnect'),
                          onPressed: (selected != null && !connectionProvider.isConnecting)
                              ? () async {
                                  await connectionProvider.connect();
                                }
                              : null,
                          style: ElevatedButton.styleFrom(
                            minimumSize: const Size(double.infinity, 48),
                          ),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: ElevatedButton.icon(
                          icon: const Icon(Icons.link_off),
                          label: const Text('Disconnect'),
                          onPressed: connectionProvider.isConnected
                              ? () => connectionProvider.clearSelectedProfile()
                              : null,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: connectionProvider.isConnected ? Colors.red : null,
                            minimumSize: const Size(double.infinity, 48),
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              );
            },
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
          if (!kReleaseMode) ...[
            const SizedBox(height: 12),
            ElevatedButton.icon(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const LogConsoleScreen()),
              ),
              icon: const Icon(Icons.terminal),
              label: const Text('View Logs'),
              style: ElevatedButton.styleFrom(minimumSize: const Size(double.infinity, 48)),
            ),
          ],
        ],
      ),
    );
  }

  void _showProfileDialog(BuildContext context, {ServerProfile? existing}) {
    final nameController = TextEditingController(text: existing?.name ?? '');
    final hostController = TextEditingController(text: existing?.host ?? '');
    final portController = TextEditingController(text: existing?.port.toString() ?? '8765');
    showDialog(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: Text(existing == null ? 'Add Profile' : 'Edit Profile'),
          content: SizedBox(
            width: 520,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: nameController,
                  decoration: const InputDecoration(
                    labelText: 'Name',
                    hintText: 'Home / Office / Lab',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: hostController,
                  decoration: const InputDecoration(
                    labelText: 'Host',
                    hintText: '192.168.1.100 or hostname',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: portController,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Port',
                    hintText: '8765',
                    border: OutlineInputBorder(),
                  ),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () async {
                final name = nameController.text.trim();
                final host = hostController.text.trim();
                final port = int.tryParse(portController.text.trim()) ?? 0;
                if (name.isEmpty || host.isEmpty || port < 1 || port > 65535) {
                  ScaffoldMessenger.of(dialogContext).showSnackBar(
                    const SnackBar(content: Text('Please enter valid name, host, and port (1-65535)')),
                  );
                  return;
                }
                final provider = context.read<ConnectionProvider>();
                final now = DateTime.now();
                final profile = existing == null
                    ? ServerProfile(
                        id: 'p${now.millisecondsSinceEpoch}',
                        name: name,
                        host: host,
                        port: port,
                        createdAt: now,
                        updatedAt: now,
                      )
                    : existing.copyWith(
                        name: name,
                        host: host,
                        port: port,
                        updatedAt: now,
                      );
                await provider.addOrUpdateProfile(profile);
                if (context.mounted) Navigator.pop(dialogContext);
              },
              child: const Text('Save'),
            ),
          ],
        );
      },
    );
  }
}
