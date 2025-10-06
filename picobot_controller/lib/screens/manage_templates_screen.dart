import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/template_provider.dart';

/// Screen for managing and deleting templates
class ManageTemplatesScreen extends StatelessWidget {
  const ManageTemplatesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Manage Templates'),
        centerTitle: true,
      ),
      body: Consumer<TemplateProvider>(
        builder: (context, templateProvider, child) {
          final templates = templateProvider.templates;

          if (templates.isEmpty) {
            return const Center(
              child: Text('No templates to manage.'),
            );
          }

          return ReorderableListView.builder(
            buildDefaultDragHandles: false, // Use explicit drag handles
            itemCount: templates.length,
            itemBuilder: (context, index) {
              final template = templates[index];
              return Card(
                key: ValueKey(template.id),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16.0, vertical: 8.0),
                  child: Row(
                    children: [
                      ReorderableDragStartListener(
                        index: index,
                        child: const Icon(Icons.drag_handle),
                      ),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Text(
                          template.name,
                          style: const TextStyle(fontSize: 16),
                        ),
                      ),
                      IconButton(
                        style: IconButton.styleFrom(
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(8),
                          ),
                          backgroundColor: Colors.red.withAlpha(25),
                        ),
                        icon: const Icon(Icons.delete, color: Colors.red, size: 20),
                        onPressed: () {
                          // Show confirmation dialog before deleting
                          _showDeleteDialog(context, template.id, template.name);
                        },
                      ),
                    ],
                  ),
                ),
              );
            },
            onReorder: (oldIndex, newIndex) {
              templateProvider.reorderTemplates(oldIndex, newIndex);
            },
          );
        },
      ),
    );
  }

  /// Show dialog to confirm template deletion
  void _showDeleteDialog(BuildContext context, String id, String name) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Template'),
        content: Text('Are you sure you want to delete "$name"?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              context.read<TemplateProvider>().deleteTemplate(id);
              Navigator.pop(context);
            },
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
  }
}
