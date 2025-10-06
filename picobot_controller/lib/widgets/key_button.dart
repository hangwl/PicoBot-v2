import 'package:flutter/material.dart';
import '../models/key_config.dart';

/// A button representing a key in the controller
class KeyButton extends StatefulWidget {
  final KeyConfig keyConfig;
  final double width;
  final double height;
  final bool isEditMode;
  final VoidCallback? onPressed;
  final VoidCallback? onReleased;

  const KeyButton({
    super.key,
    required this.keyConfig,
    required this.width,
    required this.height,
    this.isEditMode = false,
    this.onPressed,
    this.onReleased,
  });

  @override
  State<KeyButton> createState() => _KeyButtonState();
}

class _KeyButtonState extends State<KeyButton> {
  bool _isPressed = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: widget.isEditMode
          ? null
          : (_) {
              setState(() => _isPressed = true);
              widget.onPressed?.call();
            },
      onTapUp: widget.isEditMode
          ? null
          : (_) {
              setState(() => _isPressed = false);
              widget.onReleased?.call();
            },
      onTapCancel: widget.isEditMode
          ? null
          : () {
              setState(() => _isPressed = false);
              widget.onReleased?.call();
            },
      child: Container(
        width: widget.width,
        height: widget.height,
        decoration: BoxDecoration(
          color: _isPressed
              ? Theme.of(context).colorScheme.primary
              : Theme.of(context).colorScheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(8),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(77),
              blurRadius: _isPressed ? 2 : 4,
              offset: Offset(0, _isPressed ? 1 : 2),
            ),
          ],
        ),
        child: Center(
          child: Text(
            widget.keyConfig.label,
            style: TextStyle(
              fontSize: widget.width > 80 ? 18 : 14,
              fontWeight: FontWeight.bold,
              color: _isPressed
                  ? Theme.of(context).colorScheme.onPrimary
                  : Theme.of(context).colorScheme.onSurfaceVariant,
            ),
            textAlign: TextAlign.center,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ),
    );
  }
}
