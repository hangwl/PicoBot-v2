import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../providers/template_provider.dart';
import '../models/template.dart';

/// Screen for managing and deleting templates
class ManageTemplatesScreen extends StatelessWidget {
  const ManageTemplatesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Manage Templates'),
        centerTitle: true,
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 8.0),
            child: Tooltip(
              message: 'Import Template (JSON)',
              child: IconButton(
                icon: const Icon(Icons.file_upload),
                onPressed: () => _showImportTemplateDialog(context),
              ),
            ),
          ),
        ],
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
                child: ListTile(
                  contentPadding: const EdgeInsets.only(left: 16, right: 8, top: 4, bottom: 4),
                  leading: ReorderableDragStartListener(
                    index: index,
                    child: const Icon(Icons.drag_handle),
                  ),
                  title: Text(
                    template.name,
                    style: const TextStyle(fontSize: 16),
                  ),
                  trailing: Transform.translate(
                    offset: const Offset(0, 0),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        // Export button
                        IconButton(
                          style: IconButton.styleFrom(
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(8),
                            ),
                            backgroundColor: Theme.of(context)
                                .colorScheme
                                .surfaceContainerHighest,
                          ),
                          tooltip: 'Export as JSON',
                          icon: const Icon(Icons.file_download, size: 20),
                          onPressed: () {
                            _showExportTemplateDialog(context, template);
                          },
                        ),
                        const SizedBox(width: 8),
                        IconButton(
                          style: IconButton.styleFrom(
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(8),
                            ),
                            backgroundColor:
                                Theme.of(context).colorScheme.surfaceContainerHighest,
                          ),
                          tooltip: 'Delete',
                          icon: const Icon(Icons.delete, color: Colors.red, size: 20),
                          onPressed: () {
                            // Show confirmation dialog before deleting
                            _showDeleteDialog(context, template.id, template.name);
                          },
                        ),
                      ],
                    ),
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

  void _showExportTemplateDialog(BuildContext context, Template template) {
    final encoder = const JsonEncoder.withIndent('  ');
    final json = encoder.convert(template.toJson());
    showDialog(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: Text('Export "${template.name}"'),
          content: SizedBox(
            width: 600,
            child: SingleChildScrollView(
              child: SelectableText(
                json,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () async {
                // Capture before async gap
                final nav = Navigator.of(dialogContext);
                final messenger = ScaffoldMessenger.of(dialogContext);
                await Clipboard.setData(ClipboardData(text: json));
                nav.pop();
                messenger.showSnackBar(
                  const SnackBar(content: Text('Copied to clipboard')),
                );
              },
              child: const Text('Copy'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('Close'),
            ),
          ],
        );
      },
    );
  }

  void _showImportTemplateDialog(BuildContext context) {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          title: const Text('Import Template (JSON)'),
          content: SizedBox(
            width: 600,
            child: TextField(
              controller: controller,
              maxLines: 12,
              decoration: const InputDecoration(
                hintText: 'Paste a single Template JSON object or a one-item array',
                border: OutlineInputBorder(),
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () async {
                final jsonText = controller.text.trim();
                if (jsonText.isEmpty) return;
                // Capture before async gap
                final provider = context.read<TemplateProvider>();
                final nav = Navigator.of(dialogContext);
                final messenger = ScaffoldMessenger.of(dialogContext);
                try {
                  final decoded = jsonDecode(jsonText);
                  late final String normalized;
                  if (decoded is Map<String, dynamic>) {
                    normalized = jsonEncode([decoded]);
                  } else if (decoded is List && decoded.length == 1 && decoded.first is Map<String, dynamic>) {
                    normalized = jsonEncode([decoded.first]);
                  } else {
                    throw const FormatException('Expected a single template object');
                  }
                  final ok = await provider.importTemplatesJson(normalized, merge: true);
                  nav.pop();
                  messenger.showSnackBar(
                    SnackBar(content: Text(ok ? 'Import successful' : 'Import failed')),
                  );
                } catch (_) {
                  messenger.showSnackBar(
                    const SnackBar(content: Text('Invalid template JSON')),
                  );
                }
              },
              child: const Text('Import'),
            ),
          ],
        );
      },
    );
  }
}
