import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/key_config.dart';
import '../providers/template_provider.dart';
import 'dart:math';
import '../utils/constants.dart';

const double _handleSize = 24.0;

/// A draggable key widget for the template editor
class DraggableKeyWidget extends StatefulWidget {
  final KeyConfig keyConfig;
  final double screenWidth;
  final double screenHeight;
  final bool isSelected;
  final Function(bool) onSelected;
  final Function(double, double) onPositionChanged;
  final Function(double, double) onResize;
  final VoidCallback onDelete;

  const DraggableKeyWidget({
    super.key,
    required this.keyConfig,
    required this.screenWidth,
    required this.screenHeight,
    required this.isSelected,
    required this.onSelected,
    required this.onPositionChanged,
    required this.onResize,
    required this.onDelete,
  });

  @override
  State<DraggableKeyWidget> createState() => _DraggableKeyWidgetState();
}

class _DraggableKeyWidgetState extends State<DraggableKeyWidget> {
  late double x;
  late double y;
  double _initialWidth = 0.0;
  double _initialHeight = 0.0;
  Offset _dragStartOffset = Offset.zero;

  @override
  void initState() {
    super.initState();
    _updateLocalPosition();
  }

  @override
  void didUpdateWidget(covariant DraggableKeyWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.keyConfig != oldWidget.keyConfig ||
        widget.screenWidth != oldWidget.screenWidth ||
        widget.screenHeight != oldWidget.screenHeight) {
      _updateLocalPosition();
    }
  }

  void _updateLocalPosition() {
    setState(() {
      x = widget.keyConfig.xPercent * widget.screenWidth;
      y = widget.keyConfig.yPercent * widget.screenHeight;
    });
  }

  @override
  Widget build(BuildContext context) {
    final templateProvider = context.watch<TemplateProvider>();
    final isEditMode = templateProvider.isEditMode;
    final width = widget.keyConfig.widthPercent * widget.screenWidth;
    final height = widget.keyConfig.heightPercent * widget.screenHeight;

    if (!isEditMode) {
      // Simplified view for when the controller is in use
      return Positioned(
        left: x,
        top: y,
        child: Material(
          elevation: 4.0,
          color: Theme.of(context).colorScheme.primary,
          borderRadius: BorderRadius.circular(8),
          child: InkWell(
            onTap: () {
              // Handle key press
            },
            child: SizedBox(
              width: width,
              height: height,
              child: Center(
                child: Text(
                  widget.keyConfig.label,
                  style: const TextStyle(
                    fontWeight: FontWeight.bold,
                    color: Colors.white,
                  ),
                ),
              ),
            ),
          ),
        ),
      );
    }

    // Full-featured view for the template editor
    final keyButton = Material(
      elevation: 8.0,
      color: widget.isSelected
          ? Theme.of(context).colorScheme.primary
          : Theme.of(context).colorScheme.surface,
      borderRadius: BorderRadius.circular(8),
      child: InkWell(
        onTap: () {
          widget.onSelected(!widget.isSelected);
        },
        child: Container(
          width: width,
          height: height,
          decoration: BoxDecoration(
            border: Border.all(
              color: widget.isSelected
                  ? Colors.white
                  : Theme.of(context).colorScheme.primary,
              width: 2.0, // Constant border width
            ),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Center(
            child: Text(
              widget.keyConfig.label,
              style: TextStyle(
                fontWeight: FontWeight.bold,
                color: widget.isSelected
                    ? Colors.white
                    : Theme.of(context).colorScheme.onSurface,
              ),
            ),
          ),
        ),
      ),
    );

    // Use a larger stack to ensure handles are tappable
    return Positioned(
      left: x - (_handleSize / 2),
      top: y - (_handleSize / 2),
      child: SizedBox(
        width: width + _handleSize,
        height: height + _handleSize,
        child: Stack(
          children: [
            // The main draggable button
            Positioned(
              left: _handleSize / 2,
              top: _handleSize / 2,
              child: GestureDetector(
                onPanUpdate: (details) {
                  if (!widget.isSelected) {
                    widget.onSelected(true);
                    return; // Don't move on the first drag frame
                  }
                  setState(() {
                    x += details.delta.dx;
                    y += details.delta.dy;
                  });
                },
                onPanEnd: (details) {
                  final clampedX = x.clamp(0.0, widget.screenWidth - width);
                  final clampedY = y.clamp(0.0, widget.screenHeight - height);
                  widget.onPositionChanged(clampedX, clampedY);
                },
                child: keyButton,
              ),
            ),
            // Delete button
            if (widget.isSelected)
              Positioned(
                top: 0,
                right: 0,
                child: GestureDetector(
                  behavior: HitTestBehavior.translucent,
                  onTap: widget.onDelete,
                  child: Container(
                    width: _handleSize,
                    height: _handleSize,
                    color: Colors.transparent, // Make area tappable
                    child: Center(
                      child: Container(
                        width: _handleSize * 0.8,
                        height: _handleSize * 0.8,
                        decoration: const BoxDecoration(
                          color: Colors.red,
                          shape: BoxShape.circle,
                        ),
                        child: const Icon(Icons.close, color: Colors.white, size: 14),
                      ),
                    ),
                  ),
                ),
              ),
            // Resize handle (bottom-right)
            if (widget.isSelected)
              Positioned(
                bottom: 0,
                right: 0,
                child: _buildResizeHandle(),
              ),
          ],
        ),
      ),
    );
  }

  Widget _buildResizeHandle() {
    return GestureDetector(
      behavior: HitTestBehavior.translucent,
      onPanStart: (details) {
        _initialWidth = widget.keyConfig.widthPercent * widget.screenWidth;
        _initialHeight = widget.keyConfig.heightPercent * widget.screenHeight;
        _dragStartOffset = details.globalPosition;
      },
      onPanUpdate: (details) {
        final dx = details.globalPosition.dx - _dragStartOffset.dx;
        final dy = details.globalPosition.dy - _dragStartOffset.dy;

        final newWidth = _initialWidth + dx;
        final newHeight = _initialHeight + dy;

        // Enforce a minimum size to prevent negative dimensions
        const minSize = minKeySize;
        final clampedWidth = max(minSize, newWidth);
        final clampedHeight = max(minSize, newHeight);

        widget.onResize(clampedWidth, clampedHeight);
      },
      child: Container(
        width: _handleSize,
        height: _handleSize,
        decoration: BoxDecoration(
          color: Colors.green.withAlpha(128),
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 2),
        ),
        child: const Icon(Icons.zoom_out_map, color: Colors.white, size: 14),
      ),
    );
  }
}
