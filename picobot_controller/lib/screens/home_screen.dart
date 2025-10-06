import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/template_provider.dart';
import '../widgets/connection_status.dart';
import 'template_editor_screen.dart';
import 'controller_screen.dart';
import 'settings_screen.dart';

/// Home screen showing template list
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

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
          IconButton(
            icon: const Icon(Icons.settings),
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
          ),
        ],
      ),
      body: Consumer<TemplateProvider>(
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
                        IconButton(
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
                        ),
                      ],
                    ),
                  ),
                ),
              );
            },
          );
        },
      ),
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
