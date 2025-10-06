import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/template.dart';

/// Service for persisting templates and app settings
class StorageService {
  static const String _templatesKey = 'templates';
  static const String _activeTemplateKey = 'active_template_id';
  static const String _serverHostKey = 'server_host';
  static const String _serverPortKey = 'server_port';
  static const String _autoConnectKey = 'auto_connect';

  SharedPreferences? _prefs;

  /// Initialize storage service
  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  /// Ensure preferences are loaded
  Future<SharedPreferences> _getPrefs() async {
    _prefs ??= await SharedPreferences.getInstance();
    return _prefs!;
  }

  // ========== Template Management ==========

  /// Save a template
  Future<bool> saveTemplate(Template template) async {
    try {
      final prefs = await _getPrefs();
      final templates = await loadTemplates();

      // Update or add template
      final index = templates.indexWhere((t) => t.id == template.id);
      if (index >= 0) {
        templates[index] = template.copyWith(updatedAt: DateTime.now());
      } else {
        templates.add(template);
      }

      // Serialize and save
      final jsonList = templates.map((t) => t.toJson()).toList();
      final jsonString = jsonEncode(jsonList);
      return await prefs.setString(_templatesKey, jsonString);
    } catch (e) {
      // TODO: Use proper logging framework
      debugPrint('Error saving template: $e');
      return false;
    }
  }

  /// Load all templates
  Future<List<Template>> loadTemplates() async {
    try {
      final prefs = await _getPrefs();
      final jsonString = prefs.getString(_templatesKey);

      if (jsonString == null || jsonString.isEmpty) {
        return [];
      }

      final jsonList = jsonDecode(jsonString) as List;
      return jsonList.map((json) => Template.fromJson(json)).toList();
    } catch (e) {
      // TODO: Use proper logging framework
      debugPrint('Error loading templates: $e');
      return [];
    }
  }

  /// Load a specific template by ID
  Future<Template?> loadTemplate(String id) async {
    final templates = await loadTemplates();
    try {
      return templates.firstWhere((t) => t.id == id);
    } catch (e) {
      return null;
    }
  }

  /// Delete a template
  Future<bool> deleteTemplate(String id) async {
    try {
      final templates = await loadTemplates();
      templates.removeWhere((t) => t.id == id);

      final prefs = await _getPrefs();
      final jsonList = templates.map((t) => t.toJson()).toList();
      final jsonString = jsonEncode(jsonList);
      return await prefs.setString(_templatesKey, jsonString);
    } catch (e) {
      // TODO: Use proper logging framework
      debugPrint('Error deleting template: $e');
      return false;
    }
  }

  /// Get active template ID
  Future<String?> getActiveTemplateId() async {
    final prefs = await _getPrefs();
    return prefs.getString(_activeTemplateKey);
  }

  /// Set active template ID
  Future<bool> setActiveTemplateId(String id) async {
    final prefs = await _getPrefs();
    return await prefs.setString(_activeTemplateKey, id);
  }

  /// Clear active template
  Future<bool> clearActiveTemplate() async {
    final prefs = await _getPrefs();
    return await prefs.remove(_activeTemplateKey);
  }

  // ========== Server Settings ==========

  /// Get server host
  Future<String> getServerHost() async {
    final prefs = await _getPrefs();
    return prefs.getString(_serverHostKey) ?? '192.168.1.100';
  }

  /// Set server host
  Future<bool> setServerHost(String host) async {
    final prefs = await _getPrefs();
    return await prefs.setString(_serverHostKey, host);
  }

  /// Get server port
  Future<int> getServerPort() async {
    final prefs = await _getPrefs();
    return prefs.getInt(_serverPortKey) ?? 8765;
  }

  /// Set server port
  Future<bool> setServerPort(int port) async {
    final prefs = await _getPrefs();
    return await prefs.setInt(_serverPortKey, port);
  }

  /// Get auto-connect setting
  Future<bool> getAutoConnect() async {
    final prefs = await _getPrefs();
    return prefs.getBool(_autoConnectKey) ?? true;
  }

  /// Set auto-connect setting
  Future<bool> setAutoConnect(bool enabled) async {
    final prefs = await _getPrefs();
    return await prefs.setBool(_autoConnectKey, enabled);
  }

  // ========== Utility ==========

  /// Clear all data (for testing/reset)
  Future<bool> clearAll() async {
    final prefs = await _getPrefs();
    return await prefs.clear();
  }
}
