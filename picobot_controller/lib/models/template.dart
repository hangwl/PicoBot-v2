import 'package:json_annotation/json_annotation.dart';
import 'package:uuid/uuid.dart';
import 'layout_config.dart';

part 'template.g.dart';

/// Represents a keyboard template with multiple layouts
@JsonSerializable()
class Template {
  /// Unique identifier
  final String id;

  /// Template name
  final String name;

  /// Template version
  final String version;

  /// Creation timestamp
  final DateTime createdAt;

  /// Last update timestamp
  final DateTime updatedAt;

  /// Layouts for different screen sizes
  final Map<String, LayoutConfig> layouts;

  Template({
    String? id,
    required this.name,
    this.version = '1.0',
    DateTime? createdAt,
    DateTime? updatedAt,
    required this.layouts,
  })  : id = id ?? const Uuid().v4(),
        createdAt = createdAt ?? DateTime.now(),
        updatedAt = updatedAt ?? DateTime.now();

  /// Get the appropriate layout for the given screen width
  LayoutConfig? getLayoutForWidth(double width) {
    // Try layouts in order: pip -> split -> fullscreen
    for (final layoutName in ['pip', 'split', 'fullscreen']) {
      final layout = layouts[layoutName];
      if (layout != null && layout.appliesTo(width)) {
        return layout;
      }
    }
    // Fallback to fullscreen or first available
    return layouts['fullscreen'] ?? layouts.values.firstOrNull;
  }

  /// Create a copy with modified fields
  Template copyWith({
    String? id,
    String? name,
    String? version,
    DateTime? createdAt,
    DateTime? updatedAt,
    Map<String, LayoutConfig>? layouts,
  }) {
    return Template(
      id: id ?? this.id,
      name: name ?? this.name,
      version: version ?? this.version,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      layouts: layouts ?? this.layouts,
    );
  }

  /// JSON serialization
  factory Template.fromJson(Map<String, dynamic> json) =>
      _$TemplateFromJson(json);

  Map<String, dynamic> toJson() => _$TemplateToJson(this);

  @override
  String toString() => 'Template(id: $id, name: $name)';
}
