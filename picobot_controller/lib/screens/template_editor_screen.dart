import 'dart:math';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/template_provider.dart';
import '../models/key_config.dart';
import '../utils/constants.dart';
import '../widgets/draggable_key_widget.dart';

/// Template editor screen for customizing key layouts
class TemplateEditorScreen extends StatefulWidget {
  const TemplateEditorScreen({super.key});

  @override
  State<TemplateEditorScreen> createState() => _TemplateEditorScreenState();
}

class _TemplateEditorScreenState extends State<TemplateEditorScreen> {
  bool _showKeyMenu = false;
  String? _selectedKeyId;
  final GlobalKey _canvasKey = GlobalKey();

  // State for draggable modal
  double _dragOffset = 0.0;
  bool _isDragging = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<TemplateProvider>().setEditMode(true);
      _updateLayout();
    });
  }

  void _updateLayout() {
    final width = MediaQuery.of(context).size.width;
    context.read<TemplateProvider>().updateLayoutForWidth(width);
  }

  // ================= Grid helpers =================
  double _snapToGrid(double value) {
    return (value / gridStep).round() * gridStep;
  }

  Rect _rectForKey(KeyConfig key, double screenWidth, double screenHeight) {
    final x = key.xPercent * screenWidth;
    final y = key.yPercent * screenHeight;
    final w = key.widthPercent * screenWidth;
    final h = key.heightPercent * screenHeight;
    return Rect.fromLTWH(x, y, w, h);
  }

  bool _overlapsWithGap(Rect a, Rect b) {
    return a.inflate(minKeyGap / 2).overlaps(b.inflate(minKeyGap / 2));
  }

  bool _isFreeAt(Rect candidate, List<KeyConfig> keys, String movingId, double screenWidth, double screenHeight) {
    for (final other in keys) {
      if (other.id == movingId) continue;
      final r = _rectForKey(other, screenWidth, screenHeight);
      if (_overlapsWithGap(candidate, r)) return false;
    }
    return true;
  }

  Offset _findNearestFreeSnappedPosition({
    required Size screen,
    required Size keySize,
    required Offset start,
    required List<KeyConfig> keys,
    required String movingId,
  }) {
    // Snap start
    double sx = _snapToGrid(start.dx);
    double sy = _snapToGrid(start.dy);
    // Clamp to bounds
    sx = sx.clamp(0.0, screen.width - keySize.width);
    sy = sy.clamp(0.0, screen.height - keySize.height);

    Rect candidate = Rect.fromLTWH(sx, sy, keySize.width, keySize.height);
    if (_isFreeAt(candidate, keys, movingId, screen.width, screen.height)) {
      return Offset(sx, sy);
    }

    // Spiral search around start in snapped steps
    const int maxRadiusSteps = 20; // 20 * gridStep in each direction
    for (int radius = 1; radius <= maxRadiusSteps; radius++) {
      final offsets = <Offset>[
        Offset(radius * gridStep, 0),
        Offset(-radius * gridStep, 0),
        Offset(0, radius * gridStep),
        Offset(0, -radius * gridStep),
        Offset(radius * gridStep, radius * gridStep),
        Offset(radius * gridStep, -radius * gridStep),
        Offset(-radius * gridStep, radius * gridStep),
        Offset(-radius * gridStep, -radius * gridStep),
      ];
      for (final d in offsets) {
        double nx = _snapToGrid(sx + d.dx).clamp(0.0, screen.width - keySize.width);
        double ny = _snapToGrid(sy + d.dy).clamp(0.0, screen.height - keySize.height);
        final rect = Rect.fromLTWH(nx, ny, keySize.width, keySize.height);
        if (_isFreeAt(rect, keys, movingId, screen.width, screen.height)) {
          return Offset(nx, ny);
        }
      }
    }
    // Fallback: return snapped clamped start even if overlapping
    return Offset(sx, sy);
  }

  void _setSelectedKey(String? keyId) {
    setState(() {
      _selectedKeyId = keyId;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Consumer<TemplateProvider>(
          builder: (context, templateProvider, child) {
            return Text('Edit: ${templateProvider.activeTemplate?.name ?? "Template"}');
          },
        ),
        actions: [
          // Toggle key menu
          IconButton(
            icon: Icon(_showKeyMenu ? Icons.close : Icons.add_circle),
            onPressed: () {
              setState(() {
                _showKeyMenu = !_showKeyMenu;
              });
            },
            tooltip: 'Add Keys',
          ),

        ],
      ),
      body: Stack(
        children: [
          // Canvas area for keys
          Consumer<TemplateProvider>(
            builder: (context, templateProvider, child) {
              final layout = templateProvider.currentLayout;

              if (layout == null) {
                return const Center(child: CircularProgressIndicator());
              }

              return LayoutBuilder(
                builder: (context, constraints) {
                  final screenWidth = constraints.maxWidth;
                  final screenHeight = constraints.maxHeight;

                  return SizedBox.expand(
                    key: _canvasKey,
                    child: Stack(
                      children: [
                        // Grid background
                        Container(
                          decoration: BoxDecoration(
                            color: Theme.of(context).colorScheme.surface,
                          ),
                        ),
                        // Draggable keys
                        ...layout.keys.map((keyConfig) {
                          return DraggableKeyWidget(
                            key: ValueKey(keyConfig.id),
                            keyConfig: keyConfig,
                            screenWidth: screenWidth,
                            screenHeight: screenHeight,
                            isSelected: keyConfig.id == _selectedKeyId,
                            onSelected: (isSelected) {
                              _setSelectedKey(isSelected ? keyConfig.id : null);
                            },
                            onPositionChanged: (newX, newY) {
                              // Snap and resolve against grid and existing keys
                              final keySize = Size(
                                keyConfig.widthPercent * screenWidth,
                                keyConfig.heightPercent * screenHeight,
                              );
                              final start = Offset(newX, newY);
                              final pos = _findNearestFreeSnappedPosition(
                                screen: Size(screenWidth, screenHeight),
                                keySize: keySize,
                                start: start,
                                keys: layout.keys,
                                movingId: keyConfig.id,
                              );
                              final updatedKey = keyConfig.copyWith(
                                xPercent: pos.dx / screenWidth,
                                yPercent: pos.dy / screenHeight,
                              );
                              templateProvider.updateKey(updatedKey);
                            },
                            onResize: (newWidth, newHeight) {
                              // Snap size to grid and clamp within bounds
                              final xPx = keyConfig.xPercent * screenWidth;
                              final yPx = keyConfig.yPercent * screenHeight;
                              final maxWidth = (screenWidth - xPx).clamp(0.0, screenWidth);
                              final maxHeight = (screenHeight - yPx).clamp(0.0, screenHeight);
                              final snappedW = _snapToGrid(newWidth.clamp(minKeySize, maxWidth));
                              final snappedH = _snapToGrid(newHeight.clamp(minKeySize, maxHeight));
                              final updatedKey = keyConfig.copyWith(
                                widthPercent: snappedW / screenWidth,
                                heightPercent: snappedH / screenHeight,
                              );
                              templateProvider.updateKey(updatedKey);
                            },
                            onDelete: () => templateProvider.removeKey(keyConfig.id),
                          );
                        }),
                        // Empty state
                        if (layout.keys.isEmpty) 
                        Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(
                                Icons.touch_app,
                                size: 64,
                                color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.5),
                              ),
                              const SizedBox(height: 16),
                              Text(
                                'Tap + to add keys',
                                style: TextStyle(
                                  fontSize: 18,
                                  color: Theme.of(context).colorScheme.onSurface.withValues(alpha: 0.5),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  );
                },
              );
            },
          ),
          // Key menu overlay
          if (_showKeyMenu)
            // Scrim to dismiss modal on tap
            Positioned.fill(
              child: GestureDetector(
                onTap: () {
                  setState(() {
                    _showKeyMenu = false;
                  });
                },
                child: Container(
                  color: Colors.black.withAlpha(128),
                ),
              ),
            ),
          if (_showKeyMenu)
            AnimatedPositioned(
              duration: _isDragging ? Duration.zero : const Duration(milliseconds: 200),
              curve: Curves.easeInOut,
              bottom: -_dragOffset,
              left: 0,
              right: 0,
              child: _buildKeyMenu(),
            ),
        ],
      ),
    );
  }

  /// Build the key selection menu
  Widget _buildKeyMenu() {
    return Container(
      height: MediaQuery.of(context).size.height * 0.8,
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.3),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: Column(
        children: [
          // Handle bar
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onVerticalDragStart: (_) {
              setState(() {
                _isDragging = true;
              });
            },
            onVerticalDragUpdate: (details) {
              setState(() {
                // Allow dragging up and down, but not above the initial position
                _dragOffset = max(0, _dragOffset + details.delta.dy);
              });
            },
            onVerticalDragEnd: (details) {
              final modalHeight = MediaQuery.of(context).size.height * 0.8;
              final animationDuration = const Duration(milliseconds: 200);

              // Dismiss if dragged more than 30% of its height
              if (_dragOffset > modalHeight * 0.3) {
                // Animate off-screen
                setState(() {
                  _dragOffset = modalHeight;
                  _isDragging = false;
                });

                // After animation, hide the menu
                Future.delayed(animationDuration, () {
                  if (mounted) {
                    setState(() {
                      _showKeyMenu = false;
                      _dragOffset = 0;
                    });
                  }
                });
              } else {
                // Animate back if not dismissed
                setState(() {
                  _isDragging = false;
                  _dragOffset = 0;
                });
              }
            },
            child: Container(
              width: double.infinity,
              color: Colors.transparent, // Make the whole top area draggable
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: Colors.grey,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
            ),
          ),
          // Key categories
          Expanded(
            child: DefaultTabController(
              length: AvailableKeys.getAllKeysGrouped().length,
              child: Column(
                children: [
                  TabBar(
                    isScrollable: true,
                    tabAlignment: TabAlignment.start,
                    tabs: AvailableKeys.getAllKeysGrouped().keys.map((category) {
                      return Tab(text: category);
                    }).toList(),
                  ),
                  Expanded(
                    child: TabBarView(
                      children: AvailableKeys.getAllKeysGrouped().entries.map((entry) {
                        return _buildKeyGrid(entry.value);
                      }).toList(),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Build grid of keys for a category
  Widget _buildKeyGrid(List<Map<String, String>> keys) {
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 4,
        crossAxisSpacing: 12,
        mainAxisSpacing: 12,
        childAspectRatio: 1.2,
      ),
      itemCount: keys.length,
      itemBuilder: (context, index) {
        final keyData = keys[index];
        final label = keyData['label']!;
        final keyCode = keyData['keyCode']!;
        final type = keyData['type'] ?? 'key';
        final shiftLabel = keyData['shiftLabel'];

        return InkWell(
          onTap: () => _addKey(label, keyCode, type, shiftLabel),
          child: Stack(
            children: [
              // Main tile surface
              Container(
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.primaryContainer,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(
                    color: Theme.of(context).colorScheme.primary,
                  ),
                ),
                child: Center(
                  child: Text(
                    label,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      color: Theme.of(context).colorScheme.onPrimaryContainer,
                    ),
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              // Alt label badge (only if present)
              if (shiftLabel != null)
                Positioned(
                  top: 6,
                  right: 6,
                  child: Tooltip(
                    message: 'Alt: $shiftLabel',
                    child: Semantics(
                      label: 'Alternate label $shiftLabel',
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.secondaryContainer,
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          shiftLabel,
                          style: TextStyle(
                            fontSize: 11,
                            color: Theme.of(context).colorScheme.onSecondaryContainer,
                            fontWeight: FontWeight.w600,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    );
  }

  /// Add a key to the template
  void _addKey(String label, String keyCode, String type, String? shiftLabel) {
    final templateProvider = context.read<TemplateProvider>();
    final layout = templateProvider.currentLayout;
    
    if (layout == null) return;

    // Spawn new keys at a fixed central location
    final screenWidth = _canvasKey.currentContext!.size!.width;
    final screenHeight = _canvasKey.currentContext!.size!.height;

    // Desired size (snapped)
    final desiredW = _snapToGrid(DefaultKeySizes.width);
    final desiredH = _snapToGrid(DefaultKeySizes.height);
    final keySize = Size(desiredW, desiredH);

    // Start near center
    final start = Offset(0.4 * screenWidth, 0.4 * screenHeight);
    final pos = _findNearestFreeSnappedPosition(
      screen: Size(screenWidth, screenHeight),
      keySize: keySize,
      start: start,
      keys: layout.keys,
      movingId: '__new__',
    );

    final newKey = KeyConfig(
      label: label,
      keyCode: keyCode,
      type: type,
      shiftLabel: shiftLabel,
      xPercent: pos.dx / screenWidth,
      yPercent: pos.dy / screenHeight,
      widthPercent: keySize.width / screenWidth,
      heightPercent: keySize.height / screenHeight,
    );

    templateProvider.addKey(newKey);

    // Close menu after adding
    setState(() {
      _showKeyMenu = false;
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Added $label key'),
        duration: const Duration(seconds: 1),
      ),
    );
  }
}
