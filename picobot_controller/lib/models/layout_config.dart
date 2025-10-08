import 'package:json_annotation/json_annotation.dart';
import 'key_config.dart';

part 'layout_config.g.dart';

/// Represents a layout configuration for a specific screen size
@JsonSerializable()
class LayoutConfig {
  /// Minimum width in dp for this layout (null = no minimum)
  final double? minWidth;

  /// Maximum width in dp for this layout (null = no maximum)
  final double? maxWidth;

  /// List of keys in this layout
  final List<KeyConfig> keys;

  LayoutConfig({
    this.minWidth,
    this.maxWidth,
    required this.keys,
  });

  /// Check if this layout applies to the given width
  bool appliesTo(double width) {
    if (minWidth != null && width < minWidth!) return false;
    if (maxWidth != null && width > maxWidth!) return false;
    return true;
  }

  /// Create a copy with modified fields
  LayoutConfig copyWith({
    double? minWidth,
    double? maxWidth,
    List<KeyConfig>? keys,
  }) {
    return LayoutConfig(
      minWidth: minWidth ?? this.minWidth,
      maxWidth: maxWidth ?? this.maxWidth,
      keys: keys ?? this.keys,
    );
  }

  /// JSON serialization
  factory LayoutConfig.fromJson(Map<String, dynamic> json) =>
      _$LayoutConfigFromJson(json);

  Map<String, dynamic> toJson() => _$LayoutConfigToJson(this);
}
