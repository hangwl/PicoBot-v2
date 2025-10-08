import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter/services.dart' show rootBundle;
import '../models/server_profile.dart';
import '../models/template.dart';
import 'logger_service.dart';

/// Service for persisting templates and app settings
class StorageService {
  static const String _templatesKey = 'templates';
  static const String _activeTemplateKey = 'active_template_id';
  static const String _serverHostKey = 'server_host';
  static const String _serverPortKey = 'server_port';
  static const String _autoConnectKey = 'auto_connect';
  static const String _serverProfilesKey = 'server_profiles';
  static const String _selectedServerProfileIdKey = 'selected_server_profile_id';

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
    } catch (e, st) {
      LoggerService().e('Storage', 'Error saving template ${template.id}', e, st);
      return false;
    }
  }

  /// Load all templates
  Future<List<Template>> loadTemplates() async {
    try {
      final prefs = await _getPrefs();
      final jsonString = prefs.getString(_templatesKey);

      if (jsonString == null || jsonString.isEmpty) {
        return [await _loadDefaultTemplateFromAsset()];
      }

      final jsonList = jsonDecode(jsonString) as List;
      return jsonList.map((json) => Template.fromJson(json)).toList();
    } catch (e, st) {
      LoggerService().e('Storage', 'Error loading templates', e, st);
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
      final initialLength = templates.length;
      templates.removeWhere((t) => t.id == id);
      
      // Check if any template was actually removed
      if (templates.length == initialLength) {
        LoggerService().w('Storage', 'Template with id $id not found');
        return false;
      }

      final prefs = await _getPrefs();
      final jsonList = templates.map((t) => t.toJson()).toList();
      final jsonString = jsonEncode(jsonList);
      return await prefs.setString(_templatesKey, jsonString);
    } catch (e, st) {
      LoggerService().e('Storage', 'Error deleting template $id', e, st);
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

  /// Save all templates (for reordering)
  Future<bool> saveAllTemplates(List<Template> templates) async {
    try {
      final prefs = await _getPrefs();
      final jsonList = templates.map((t) => t.toJson()).toList();
      final jsonString = jsonEncode(jsonList);
      return await prefs.setString(_templatesKey, jsonString);
    } catch (e, st) {
      LoggerService().e('Storage', 'Error saving all templates', e, st);
      return false;
    }
  }

  /// Clear active template
  Future<bool> clearActiveTemplate() async {
    final prefs = await _getPrefs();
    return await prefs.remove(_activeTemplateKey);
  }

  // ========== Server Settings ==========

  // ---- Profiles (new) ----
  Future<List<ServerProfile>> getServerProfiles() async {
    try {
      final prefs = await _getPrefs();
      final jsonString = prefs.getString(_serverProfilesKey);
      if (jsonString == null || jsonString.isEmpty) return [];
      final list = (jsonDecode(jsonString) as List)
          .map((e) => ServerProfile.fromJson(e as Map<String, dynamic>))
          .toList();
      return list;
    } catch (e, st) {
      LoggerService().e('Storage', 'Error loading server profiles', e, st);
      return [];
    }
  }

  Future<bool> saveServerProfiles(List<ServerProfile> profiles) async {
    try {
      final prefs = await _getPrefs();
      final jsonList = profiles.map((p) => p.toJson()).toList();
      final jsonString = jsonEncode(jsonList);
      return await prefs.setString(_serverProfilesKey, jsonString);
    } catch (e, st) {
      LoggerService().e('Storage', 'Error saving server profiles', e, st);
      return false;
    }
  }

  Future<String?> getSelectedServerProfileId() async {
    final prefs = await _getPrefs();
    return prefs.getString(_selectedServerProfileIdKey);
  }

  Future<bool> setSelectedServerProfileId(String? id) async {
    final prefs = await _getPrefs();
    if (id == null || id.isEmpty) {
      return await prefs.remove(_selectedServerProfileIdKey);
    }
    return await prefs.setString(_selectedServerProfileIdKey, id);
  }

  Future<void> migrateLegacyServerSettingsIfNeeded() async {
    try {
      final prefs = await _getPrefs();
      final existing = prefs.getString(_serverProfilesKey);
      if (existing != null && existing.isNotEmpty) return; // already migrated

      // Read legacy values
      final host = prefs.getString(_serverHostKey) ?? '192.168.1.100';
      final port = prefs.getInt(_serverPortKey) ?? 8765;
      final auto = prefs.getBool(_autoConnectKey) ?? true;

      final now = DateTime.now();
      final profile = ServerProfile(
        id: 'p${now.millisecondsSinceEpoch}',
        name: 'Default',
        host: host,
        port: port,
        createdAt: now,
        updatedAt: now,
      );
      await saveServerProfiles([profile]);
      await setSelectedServerProfileId(auto ? profile.id : null);
    } catch (e) {
      LoggerService().w('Storage', 'Server settings migration skipped: $e');
    }
  }

  // ---- Legacy single host/port (kept for backward compat, will be unused) ----
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

  /// Load the default template from assets
  Future<Template> _loadDefaultTemplateFromAsset() async {
    final jsonString = await rootBundle.loadString('assets/templates/default_template.json');
    final jsonMap = jsonDecode(jsonString);
    return Template.fromJson(jsonMap);
  }

  /// Clear all data (for testing/reset)
  Future<bool> clearAll() async {
    final prefs = await _getPrefs();
    return await prefs.clear();
  }

  // ========== Import/Export ==========

  /// Export all templates to a JSON string
  Future<String> exportTemplatesJson({bool pretty = true}) async {
    try {
      final templates = await loadTemplates();
      final jsonList = templates.map((t) => t.toJson()).toList();
      if (pretty) {
        const encoder = JsonEncoder.withIndent('  ');
        return encoder.convert(jsonList);
      }
      return jsonEncode(jsonList);
    } catch (e, st) {
      LoggerService().e('Storage', 'Error exporting templates', e, st);
      return '[]';
    }
  }

  /// Import templates from a JSON string.
  ///
  /// The JSON must be an array of Template objects.
  /// If [merge] is true, incoming templates replace existing ones with the same ID
  /// and add new ones; otherwise the existing set is fully replaced.
  Future<bool> importTemplatesJson(String jsonString, {bool merge = true}) async {
    try {
      final prefs = await _getPrefs();
      final dynamic decoded = jsonDecode(jsonString);
      if (decoded is! List) {
        throw FormatException('Expected a JSON array of templates');
      }
      final incoming = decoded.map((e) => Template.fromJson(e as Map<String, dynamic>)).toList();

      if (!merge) {
        // Replace all
        final newJson = jsonEncode(incoming.map((t) => t.toJson()).toList());
        final ok = await prefs.setString(_templatesKey, newJson);
        return ok;
      }

      // Merge with existing
      final existing = await loadTemplates();
      final byId = {for (final t in existing) t.id: t};
      for (final t in incoming) {
        byId[t.id] = t; // replace or add
      }
      final merged = byId.values.toList();
      final newJson = jsonEncode(merged.map((t) => t.toJson()).toList());
      final ok = await prefs.setString(_templatesKey, newJson);
      return ok;
    } catch (e, st) {
      LoggerService().e('Storage', 'Error importing templates', e, st);
      return false;
    }
  }
}
