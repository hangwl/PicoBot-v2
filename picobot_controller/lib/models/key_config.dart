import 'package:json_annotation/json_annotation.dart';
import 'package:uuid/uuid.dart';

part 'key_config.g.dart';

/// Represents a single key button in a template
@JsonSerializable()
class KeyConfig {
  /// Unique identifier for this key
  final String id;

  /// Display label on the button
  final String label;

  /// Alternate label to display when Shift is active
  final String? shiftLabel;

  /// Actual key code to send (e.g., "w", "space", "enter")
  final String keyCode;

  /// Type of input: "key" or "mouse"
  final String type;

  /// X position as percentage of screen width (0.0 to 1.0)
  final double xPercent;

  /// Y position as percentage of screen height (0.0 to 1.0)
  final double yPercent;

  /// Width as percentage of screen width (0.0 to 1.0)
  final double widthPercent;

  /// Height as percentage of screen height (0.0 to 1.0)
  final double heightPercent;

  KeyConfig({
    String? id,
    required this.label,
    required this.keyCode,
    this.type = 'key',
    required this.xPercent,
    required this.yPercent,
    required this.widthPercent,
    required this.heightPercent,
    this.shiftLabel,
  }) : id = id ?? const Uuid().v4();

  /// Create a copy with modified fields
  KeyConfig copyWith({
    String? id,
    String? label,
    String? shiftLabel,
    String? keyCode,
    String? type,
    double? xPercent,
    double? yPercent,
    double? widthPercent,
    double? heightPercent,
  }) {
    return KeyConfig(
      id: id ?? this.id,
      label: label ?? this.label,
      shiftLabel: shiftLabel ?? this.shiftLabel,
      keyCode: keyCode ?? this.keyCode,
      type: type ?? this.type,
      xPercent: xPercent ?? this.xPercent,
      yPercent: yPercent ?? this.yPercent,
      widthPercent: widthPercent ?? this.widthPercent,
      heightPercent: heightPercent ?? this.heightPercent,
    );
  }

  /// JSON serialization
  factory KeyConfig.fromJson(Map<String, dynamic> json) =>
      _$KeyConfigFromJson(json);

  Map<String, dynamic> toJson() => _$KeyConfigToJson(this);

  @override
  String toString() => 'KeyConfig(id: $id, label: $label, keyCode: $keyCode)';
}
