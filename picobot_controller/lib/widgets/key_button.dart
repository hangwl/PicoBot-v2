import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/key_config.dart';
import '../providers/connection_provider.dart';

/// A button representing a key in the controller
class KeyButton extends StatelessWidget {
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
  Widget build(BuildContext context) {
    return Consumer<ConnectionProvider>(
      builder: (context, connectionProvider, child) {
        final isShiftPressed = connectionProvider.isShiftPressed;
        final isPressed = connectionProvider.isKeyPressed(keyConfig.id);

        return GestureDetector(
          onTapDown: isEditMode ? null : (_) => onPressed?.call(),
          onTapUp: isEditMode ? null : (_) => onReleased?.call(),
          onTapCancel: isEditMode ? null : () => onReleased?.call(),
          child: Container(
            width: width,
            height: height,
            decoration: BoxDecoration(
              color: isPressed
                  ? Colors.white.withOpacity(0.2)
                  : Theme.of(context).colorScheme.surfaceContainerHighest,
              borderRadius: BorderRadius.circular(8),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withAlpha(77),
                  blurRadius: isPressed ? 2 : 4,
                  offset: Offset(0, isPressed ? 1 : 2),
                ),
              ],
            ),
            child: Center(
              child: Text(
                (isShiftPressed && keyConfig.shiftLabel != null)
                    ? keyConfig.shiftLabel!
                    : keyConfig.label,
                style: TextStyle(
                  fontSize: width > 80 ? 18 : 14,
                  fontWeight: FontWeight.bold,
                  color: isPressed
                      ? Colors.white
                      : Theme.of(context).colorScheme.onSurfaceVariant,
                ),
                textAlign: TextAlign.center,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ),
        );
      },
    );
  }
}
