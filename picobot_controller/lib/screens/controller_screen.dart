import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/connection_provider.dart';
import '../providers/template_provider.dart';
import '../widgets/key_button.dart';

/// Controller screen for using the template (locked mode)
class ControllerScreen extends StatefulWidget {
  const ControllerScreen({super.key});

  @override
  State<ControllerScreen> createState() => _ControllerScreenState();
}

class _ControllerScreenState extends State<ControllerScreen> {
  @override
  void initState() {
    super.initState();
    // Update layout for current screen size
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _updateLayout();
    });
  }

  void _updateLayout() {
    final width = MediaQuery.of(context).size.width;
    context.read<TemplateProvider>().updateLayoutForWidth(width);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Consumer<TemplateProvider>(
          builder: (context, templateProvider, child) {
            return Text(templateProvider.activeTemplate?.name ?? 'Controller');
          },
        ),
        actions: [
          // Macro control button
          Consumer<ConnectionProvider>(
            builder: (context, connectionProvider, child) {
              final isMacroPlaying = connectionProvider.isMacroPlaying;
              
              return IconButton(
                icon: Icon(isMacroPlaying ? Icons.stop : Icons.play_arrow),
                onPressed: connectionProvider.isConnected
                    ? () {
                        if (isMacroPlaying) {
                          connectionProvider.stopMacro();
                        } else {
                          connectionProvider.startMacro();
                        }
                      }
                    : null,
                tooltip: isMacroPlaying ? 'Stop Macro' : 'Start Macro',
              );
            },
          ),
        ],
      ),
      body: Consumer2<TemplateProvider, ConnectionProvider>(
        builder: (context, templateProvider, connectionProvider, child) {
          final layout = templateProvider.currentLayout;
          
          if (layout == null || layout.keys.isEmpty) {
            return Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Icon(Icons.keyboard, size: 64, color: Colors.grey),
                  const SizedBox(height: 16),
                  const Text(
                    'No keys in this template',
                    style: TextStyle(fontSize: 18),
                  ),
                  const SizedBox(height: 8),
                  ElevatedButton.icon(
                    onPressed: () {
                      templateProvider.setEditMode(true);
                      Navigator.pop(context);
                    },
                    icon: const Icon(Icons.edit),
                    label: const Text('Edit Template'),
                  ),
                ],
              ),
            );
          }

          return LayoutBuilder(
            builder: (context, constraints) {
              final screenWidth = constraints.maxWidth;
              final screenHeight = constraints.maxHeight;

              return Stack(
                children: layout.keys.map((keyConfig) {
                  final x = keyConfig.xPercent * screenWidth;
                  final y = keyConfig.yPercent * screenHeight;
                  final width = keyConfig.widthPercent * screenWidth;
                  final height = keyConfig.heightPercent * screenHeight;

                  return Positioned(
                    left: x,
                    top: y,
                    child: KeyButton(
                      keyConfig: keyConfig,
                      width: width,
                      height: height,
                      isEditMode: false,
                      onPressed: connectionProvider.isConnected
                          ? () {
                              if (keyConfig.type == 'mouse') {
                                connectionProvider.sendMouseDown(keyConfig.keyCode);
                              } else {
                                connectionProvider.sendKeyDown(keyConfig.keyCode);
                              }
                            }
                          : null,
                      onReleased: connectionProvider.isConnected
                          ? () {
                              if (keyConfig.type == 'mouse') {
                                connectionProvider.sendMouseUp(keyConfig.keyCode);
                              } else {
                                connectionProvider.sendKeyUp(keyConfig.keyCode);
                              }
                            }
                          : null,
                    ),
                  );
                }).toList(),
              );
            },
          );
        },
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: () {
          context.read<TemplateProvider>().setEditMode(true);
          Navigator.pop(context);
        },
        child: const Icon(Icons.edit),
      ),
    );
  }
}
