import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/connection_provider.dart';
import '../providers/template_provider.dart';
import '../widgets/key_button.dart';
import '../models/key_config.dart';

/// Controller screen for using the template (locked mode)
class ControllerScreen extends StatefulWidget {
  const ControllerScreen({super.key});

  @override
  State<ControllerScreen> createState() => _ControllerScreenState();
}

class _ControllerScreenState extends State<ControllerScreen> {
  // Active pointers state and per-key active counts (for multi-touch on same key)
  final Map<int, _PointerState> _pointers = {};
  final Map<String, int> _keyActiveCounts = {};

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
        title: Consumer<ConnectionProvider>(
          builder: (context, connectionProvider, child) {
            final isMacroPlaying = connectionProvider.isMacroPlaying;
            return SizedBox(
              width: 200,
              child: ElevatedButton.icon(
                icon: Icon(isMacroPlaying ? Icons.stop : Icons.play_arrow),
                label: Text(isMacroPlaying ? 'STOP' : 'START'),
                onPressed: connectionProvider.isConnected
                    ? () {
                        if (isMacroPlaying) {
                          connectionProvider.stopMacro();
                        } else {
                          connectionProvider.startMacro();
                        }
                      }
                    : null,
                style: ElevatedButton.styleFrom(
                  foregroundColor: isMacroPlaying
                      ? Theme.of(context).colorScheme.onError
                      : Theme.of(context).colorScheme.onPrimary,
                  backgroundColor: isMacroPlaying
                      ? Theme.of(context).colorScheme.error
                      : Colors.green,
                ),
              ),
            );
          },
        ),
        centerTitle: true,
        actions: [
          // Connection + latency indicator
          Consumer<ConnectionProvider>(
            builder: (context, connectionProvider, child) {
              final rtt = connectionProvider.lastPingRtt;
              final rttMs = rtt?.inMilliseconds;
              return Padding(
                padding: const EdgeInsets.only(right: 16.0),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.circle,
                      color: connectionProvider.isConnected ? Colors.green : Colors.red,
                      size: 16,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      rttMs != null ? '${rttMs}ms' : '--',
                      style: Theme.of(context).textTheme.labelMedium,
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ),
      body: Consumer2<TemplateProvider, ConnectionProvider>(
        builder: (context, templateProvider, connectionProvider, child) {
          // Show loading indicator while provider is initializing
          if (connectionProvider.isLoading) {
            return const Center(child: CircularProgressIndicator());
          }

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
                ],
              ),
            );
          }

          return LayoutBuilder(
            builder: (context, constraints) {
              final screenWidth = constraints.maxWidth;
              final screenHeight = constraints.maxHeight;

              return Listener(
                behavior: HitTestBehavior.opaque,
                onPointerDown: (event) {
                  _handlePointerDown(
                    pointer: event.pointer,
                    pos: event.localPosition,
                    screenWidth: screenWidth,
                    screenHeight: screenHeight,
                    templateProvider: context.read<TemplateProvider>(),
                    connectionProvider: context.read<ConnectionProvider>(),
                  );
                },
                onPointerMove: (event) {
                  // No-op for central controller: movement does not change press state
                },
                onPointerUp: (event) {
                  _handlePointerUp(
                    pointer: event.pointer,
                    connectionProvider: context.read<ConnectionProvider>(),
                  );
                },
                onPointerCancel: (event) {
                  _handlePointerCancel(
                    pointer: event.pointer,
                    connectionProvider: context.read<ConnectionProvider>(),
                  );
                },
                child: SizedBox.expand(
                  child: Stack(
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
                          isEditMode: true,
                          // Central Listener handles input; no per-key gesture callbacks
                        ),
                      );
                    }).toList(),
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }

  // ================= Pointer handling and hit-testing =================

  void _handlePointerDown({
    required int pointer,
    required Offset pos,
    required double screenWidth,
    required double screenHeight,
    required TemplateProvider templateProvider,
    required ConnectionProvider connectionProvider,
  }) {
    final key = _hitTestKey(pos, screenWidth, screenHeight, templateProvider);
    final st = _PointerState(pointer, key);
    _pointers[pointer] = st;

    if (key != null) {
      final count = (_keyActiveCounts[key.id] ?? 0) + 1;
      _keyActiveCounts[key.id] = count;
      if (count == 1) {
        // First active pointer for this key: send press immediately
        if (!connectionProvider.isKeyPressed(key.id)) {
          connectionProvider.handleKeyPress(key);
        }
      }
    }
  }

  void _handlePointerUp({
    required int pointer,
    required ConnectionProvider connectionProvider,
  }) {
    final st = _pointers.remove(pointer);
    final key = st?.key;
    if (key == null) return;

    final current = (_keyActiveCounts[key.id] ?? 0) - 1;
    if (current <= 0) {
      _keyActiveCounts.remove(key.id);
      if (connectionProvider.isKeyPressed(key.id)) {
        connectionProvider.handleKeyRelease(key);
      }
    } else {
      _keyActiveCounts[key.id] = current;
    }
  }

  void _handlePointerCancel({
    required int pointer,
    required ConnectionProvider connectionProvider,
  }) {
    final st = _pointers.remove(pointer);
    final key = st?.key;
    if (key == null) return;

    final current = (_keyActiveCounts[key.id] ?? 0) - 1;
    if (current <= 0) {
      _keyActiveCounts.remove(key.id);
      if (connectionProvider.isKeyPressed(key.id)) {
        connectionProvider.handleKeyRelease(key);
      }
    } else {
      _keyActiveCounts[key.id] = current;
    }
  }

  KeyConfig? _hitTestKey(
    Offset pos,
    double screenWidth,
    double screenHeight,
    TemplateProvider templateProvider,
  ) {
    final layout = templateProvider.currentLayout;
    if (layout == null) return null;
    // Iterate in reverse so visually top-most (later) keys win
    for (final key in layout.keys.reversed) {
      final rect = _rectForKey(key, screenWidth, screenHeight);
      if (rect.contains(pos)) return key;
    }
    return null;
  }

  Rect _rectForKey(KeyConfig key, double screenWidth, double screenHeight) {
    final x = key.xPercent * screenWidth;
    final y = key.yPercent * screenHeight;
    final w = key.widthPercent * screenWidth;
    final h = key.heightPercent * screenHeight;
    return Rect.fromLTWH(x, y, w, h);
  }

}

class _PointerState {
  _PointerState(this.pointer, this.key);
  final int pointer;
  final KeyConfig? key;
}
