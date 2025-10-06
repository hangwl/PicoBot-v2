import 'package:flutter/material.dart';
import '../models/key_config.dart';

/// A draggable key widget for the template editor
class DraggableKeyWidget extends StatefulWidget {
  final KeyConfig keyConfig;
  final double screenWidth;
  final double screenHeight;
  final Function(double x, double y) onPositionChanged;
  final VoidCallback onDelete;

  const DraggableKeyWidget({
    super.key,
    required this.keyConfig,
    required this.screenWidth,
    required this.screenHeight,
    required this.onPositionChanged,
    required this.onDelete,
  });

  @override
  State<DraggableKeyWidget> createState() => _DraggableKeyWidgetState();
}

class _DraggableKeyWidgetState extends State<DraggableKeyWidget> {
  late double _x;
  late double _y;
  bool _isDragging = false;

  @override
  void initState() {
    super.initState();
    _x = widget.keyConfig.xPercent * widget.screenWidth;
    _y = widget.keyConfig.yPercent * widget.screenHeight;
  }

  @override
  void didUpdateWidget(DraggableKeyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.screenWidth != widget.screenWidth ||
        oldWidget.screenHeight != widget.screenHeight) {
      _x = widget.keyConfig.xPercent * widget.screenWidth;
      _y = widget.keyConfig.yPercent * widget.screenHeight;
    }
  }

  @override
  Widget build(BuildContext context) {
    final width = widget.keyConfig.widthPercent * widget.screenWidth;
    final height = widget.keyConfig.heightPercent * widget.screenHeight;

    return Positioned(
      left: _x,
      top: _y,
      child: GestureDetector(
        onPanStart: (_) {
          setState(() {
            _isDragging = true;
          });
        },
        onPanUpdate: (details) {
          setState(() {
            _x += details.delta.dx;
            _y += details.delta.dy;

            // Clamp to screen bounds
            _x = _x.clamp(0, widget.screenWidth - width);
            _y = _y.clamp(0, widget.screenHeight - height);
          });
        },
        onPanEnd: (_) {
          setState(() {
            _isDragging = false;
          });
          widget.onPositionChanged(_x, _y);
        },
        onLongPress: () {
          _showKeyOptions(context);
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 100),
          width: width,
          height: height,
          decoration: BoxDecoration(
            color: _isDragging
                ? Theme.of(context).colorScheme.primary.withValues(alpha: 0.8)
                : Theme.of(context).colorScheme.secondaryContainer,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: Theme.of(context).colorScheme.primary,
              width: _isDragging ? 3 : 2,
            ),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: _isDragging ? 0.4 : 0.2),
                blurRadius: _isDragging ? 8 : 4,
                offset: Offset(0, _isDragging ? 4 : 2),
              ),
            ],
          ),
          child: Stack(
            children: [
              // Key label
              Center(
                child: Text(
                  widget.keyConfig.label,
                  style: TextStyle(
                    fontSize: width > 80 ? 18 : 14,
                    fontWeight: FontWeight.bold,
                    color: _isDragging
                        ? Theme.of(context).colorScheme.onPrimary
                        : Theme.of(context).colorScheme.onSecondaryContainer,
                  ),
                  textAlign: TextAlign.center,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              // Drag indicator
              Positioned(
                top: 4,
                right: 4,
                child: Icon(
                  Icons.drag_indicator,
                  size: 16,
                  color: _isDragging
                      ? Theme.of(context).colorScheme.onPrimary
                      : Theme.of(context).colorScheme.onSecondaryContainer.withValues(alpha: 0.5),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Show options menu for the key
  void _showKeyOptions(BuildContext context) {
    showModalBottomSheet(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.info),
              title: Text(widget.keyConfig.label),
              subtitle: Text('Key: ${widget.keyConfig.keyCode}'),
            ),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.delete, color: Colors.red),
              title: const Text('Delete Key'),
              onTap: () {
                Navigator.pop(context);
                _confirmDelete(context);
              },
            ),
            ListTile(
              leading: const Icon(Icons.close),
              title: const Text('Cancel'),
              onTap: () => Navigator.pop(context),
            ),
          ],
        ),
      ),
    );
  }

  /// Confirm deletion
  void _confirmDelete(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete Key'),
        content: Text('Remove "${widget.keyConfig.label}" from template?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              Navigator.pop(context);
              widget.onDelete();
            },
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
  }
}
