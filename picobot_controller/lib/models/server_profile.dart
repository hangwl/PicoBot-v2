import 'dart:convert';

class ServerProfile {
  final String id;
  final String name;
  final String host;
  final int port;
  final DateTime createdAt;
  final DateTime updatedAt;

  const ServerProfile({
    required this.id,
    required this.name,
    required this.host,
    required this.port,
    required this.createdAt,
    required this.updatedAt,
  });

  ServerProfile copyWith({
    String? id,
    String? name,
    String? host,
    int? port,
    DateTime? createdAt,
    DateTime? updatedAt,
  }) {
    return ServerProfile(
      id: id ?? this.id,
      name: name ?? this.name,
      host: host ?? this.host,
      port: port ?? this.port,
      createdAt: createdAt ?? this.createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'id': id,
      'name': name,
      'host': host,
      'port': port,
      'createdAt': createdAt.toIso8601String(),
      'updatedAt': updatedAt.toIso8601String(),
    };
  }

  static ServerProfile fromJson(Map<String, dynamic> json) {
    return ServerProfile(
      id: json['id'] as String,
      name: json['name'] as String,
      host: json['host'] as String,
      port: (json['port'] as num).toInt(),
      createdAt: DateTime.parse(json['createdAt'] as String),
      updatedAt: DateTime.parse(json['updatedAt'] as String),
    );
  }

  @override
  String toString() => jsonEncode(toJson());
}
