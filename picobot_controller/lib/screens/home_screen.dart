import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/connection_provider.dart';
import '../providers/template_provider.dart';
import '../widgets/connection_status.dart';
import 'template_editor_screen.dart';
import 'controller_screen.dart';
import 'settings_screen.dart';

/// Home screen showing template list
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  Widget _buildPlaylistSelector(BuildContext context) {
    return Consumer<ConnectionProvider>(
      builder: (context, connection, child) {
        if (!connection.isConnected || connection.playlists.isEmpty) {
          return const SizedBox.shrink(); // Hide if not connected or no playlists
        }

        return Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: InputDecorator(
            decoration: const InputDecoration(
              labelText: 'Macro Playlist',
              border: OutlineInputBorder(),
              contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            ),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                value: connection.selectedPlaylist,
                hint: const Text('Select Macro Playlist'),
                isExpanded: true,
                items: connection.playlists.map((playlist) {
                  return DropdownMenuItem(value: playlist, child: Text(playlist));
                }).toList(),
                onChanged: (value) {
                  connection.selectPlaylist(value);
                },
              ),
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        leading: const Padding(
          padding: EdgeInsets.only(left: 16.0),
          child: ConnectionStatusWidget(),
        ),
        actions: [
          // Add new template button
          IconButton(
            icon: const Icon(Icons.add),
            onPressed: () => _showCreateTemplateDialog(context),
            tooltip: 'New Template',
          ),
          // Settings button
          Padding(
            padding: const EdgeInsets.only(right: 8.0),
            child: Tooltip(
              message: 'Settings',
              child: IconButton(
                icon: const Icon(Icons.settings),
                onPressed: () {
                Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const SettingsScreen()),
                );
              },
            ),), // End Tooltip
          ),
        ],
      ),
      body: Column(
        children: [
          _buildPlaylistSelector(context),
          Expanded(
            child: Consumer<TemplateProvider>(
        builder: (context, templateProvider, child) {
          if (templateProvider.isLoading) {
            return const Center(child: CircularProgressIndicator());
          }

          final templates = templateProvider.templates;

          if (templates.isEmpty) {
            return Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    Icons.keyboard,
                    size: 64,
                    color: Theme.of(context).colorScheme.primary,
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'No templates yet',
                    style: TextStyle(fontSize: 18),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Create your first keyboard template',
                    style: TextStyle(color: Colors.grey),
                  ),
                ],
              ),
            );
          }

          return ListView.builder(
            padding: const EdgeInsets.all(16),
            itemCount: templates.length,
            itemBuilder: (context, index) {
              final template = templates[index];
              return Card(
                child: InkWell(
                  onTap: () async {
                    await templateProvider.setActiveTemplate(template);
                    if (context.mounted) {
                      Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const ControllerScreen(),
                        ),
                      );
                    }
                  },
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 12.0),
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            template.name,
                            style: TextStyle(
                              fontWeight: FontWeight.normal,
                              fontSize: 16,
                            ),
                          ),
                        ),
                        Tooltip(
                          message: 'Edit Template',
                          child: IconButton(
                            style: IconButton.styleFrom(
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(8),
                            ),
                            backgroundColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                          ),
                          icon: const Icon(Icons.edit, size: 20),
                          onPressed: () {
                            templateProvider.setActiveTemplate(template);
                            Navigator.push(
                              context,
                              MaterialPageRoute(
                                builder: (_) => const TemplateEditorScreen(),
                              ),
                            );
                          },
                          ), // End IconButton
                        ), // End Tooltip
                      ],
                    ),
                  ),
                ),
              );
            },
          );
        },
      ),), // End Expanded
      ],), // End Column
    );
  }

  /// Show dialog to create new template
  void _showCreateTemplateDialog(BuildContext context) {
    final controller = TextEditingController();

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Create Template'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(
            labelText: 'Template Name',
            hintText: 'e.g., WASD Layout',
          ),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () async {
              final name = controller.text.trim();
              if (name.isNotEmpty) {
                final templateProvider = context.read<TemplateProvider>();
                final newTemplate = await templateProvider.createTemplate(name);
                await templateProvider.setActiveTemplate(newTemplate);
                
                if (context.mounted) {
                  Navigator.pop(context);
                }
              }
            },
            child: const Text('Create'),
          ),
        ],
      ),
    );
  }

}
