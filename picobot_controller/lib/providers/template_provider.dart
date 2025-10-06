import 'package:flutter/foundation.dart';
import '../models/template.dart';
import '../models/layout_config.dart';
import '../models/key_config.dart';
import '../services/storage_service.dart';
import '../utils/constants.dart';

/// Provider for managing templates
class TemplateProvider extends ChangeNotifier {
  final StorageService _storageService;

  List<Template> _templates = [];
  Template? _activeTemplate;
  LayoutConfig? _currentLayout;
  bool _isEditMode = false;
  bool _isLoading = false;

  TemplateProvider(this._storageService) {
    _init();
  }

  // Getters
  List<Template> get templates => _templates;
  Template? get activeTemplate => _activeTemplate;
  LayoutConfig? get currentLayout => _currentLayout;
  bool get isEditMode => _isEditMode;
  bool get isLoading => _isLoading;
  bool get hasActiveTemplate => _activeTemplate != null;

  /// Initialize provider
  Future<void> _init() async {
    await loadTemplates();
    await loadActiveTemplate();
  }

  /// Load all templates
  Future<void> loadTemplates() async {
    _isLoading = true;
    notifyListeners();

    _templates = await _storageService.loadTemplates();

    // If templates are still empty after loading, it means it's the first launch
    // and the storage service will have loaded the default from assets.
    if (_templates.isEmpty) {
      _templates = await _storageService.loadTemplates();
    }

    _isLoading = false;
    notifyListeners();
  }


  /// Load active template
  Future<void> loadActiveTemplate() async {
    final activeId = await _storageService.getActiveTemplateId();
    if (activeId != null) {
      _activeTemplate = await _storageService.loadTemplate(activeId);
    } else if (_templates.isNotEmpty) {
      // Set first template as active if none selected
      _activeTemplate = _templates.first;
      await _storageService.setActiveTemplateId(_activeTemplate!.id);
    }
    notifyListeners();
  }

  /// Set active template
  Future<void> setActiveTemplate(Template template) async {
    _activeTemplate = template;
    await _storageService.setActiveTemplateId(template.id);
    notifyListeners();
  }

  /// Update current layout based on screen width
  void updateLayoutForWidth(double width) {
    if (_activeTemplate == null) return;
    _currentLayout = _activeTemplate!.getLayoutForWidth(width);
    notifyListeners();
  }

  /// Save a template
  Future<void> saveTemplate(Template template) async {
    final success = await _storageService.saveTemplate(template);
    if (success) {
      await loadTemplates();
      // Update active template if it was modified
      if (_activeTemplate?.id == template.id) {
        _activeTemplate = template;
      }
      notifyListeners();
    }
  }

  /// Create new template
  Future<Template> createTemplate(String name) async {
    final newTemplate = Template(
      name: name,
      layouts: {
        'fullscreen': LayoutConfig(
          minWidth: ScreenBreakpoints.fullscreenMinWidth,
          keys: [],
        ),
      },
    );

    await saveTemplate(newTemplate);
    return newTemplate;
  }

  /// Delete template
  Future<void> deleteTemplate(String id) async {
    final success = await _storageService.deleteTemplate(id);
    if (success) {
      await loadTemplates();
      // Clear active template if it was deleted
      if (_activeTemplate?.id == id) {
        _activeTemplate = null;
        await _storageService.clearActiveTemplate();
        if (_templates.isNotEmpty) {
          await setActiveTemplate(_templates.first);
        }
      }
      notifyListeners();
    }
  }

  /// Toggle edit mode
  void toggleEditMode() {
    _isEditMode = !_isEditMode;
    notifyListeners();
  }

  /// Set edit mode
  void setEditMode(bool enabled) {
    _isEditMode = enabled;
    notifyListeners();
  }

  /// Add key to active template
  Future<void> addKey(KeyConfig key) async {
    if (_activeTemplate == null || _currentLayout == null) return;

    final updatedKeys = [..._currentLayout!.keys, key];
    final updatedLayout = _currentLayout!.copyWith(keys: updatedKeys);

    final updatedLayouts = Map<String, LayoutConfig>.from(_activeTemplate!.layouts);
    updatedLayouts['fullscreen'] = updatedLayout;

    final updatedTemplate = _activeTemplate!.copyWith(layouts: updatedLayouts);
    await saveTemplate(updatedTemplate);
    _activeTemplate = updatedTemplate;
    _currentLayout = updatedLayout;
    notifyListeners();
  }

  /// Remove key from active template
  Future<void> removeKey(String keyId) async {
    if (_activeTemplate == null || _currentLayout == null) return;

    final updatedKeys = _currentLayout!.keys.where((k) => k.id != keyId).toList();
    final updatedLayout = _currentLayout!.copyWith(keys: updatedKeys);

    final updatedLayouts = Map<String, LayoutConfig>.from(_activeTemplate!.layouts);
    updatedLayouts['fullscreen'] = updatedLayout;

    final updatedTemplate = _activeTemplate!.copyWith(layouts: updatedLayouts);
    await saveTemplate(updatedTemplate);
    _activeTemplate = updatedTemplate;
    _currentLayout = updatedLayout;
    notifyListeners();
  }

  /// Reorder templates
  Future<void> reorderTemplates(int oldIndex, int newIndex) async {
    if (newIndex > oldIndex) {
      newIndex -= 1;
    }
    final template = _templates.removeAt(oldIndex);
    _templates.insert(newIndex, template);
    await _storageService.saveAllTemplates(_templates);
    notifyListeners();
  }

  /// Update key position/size
  Future<void> updateKey(KeyConfig updatedKey) async {
    if (_activeTemplate == null || _currentLayout == null) return;

    final updatedKeys = _currentLayout!.keys.map((k) {
      return k.id == updatedKey.id ? updatedKey : k;
    }).toList();

    final updatedLayout = _currentLayout!.copyWith(keys: updatedKeys);

    final updatedLayouts = Map<String, LayoutConfig>.from(_activeTemplate!.layouts);
    updatedLayouts['fullscreen'] = updatedLayout;

    final updatedTemplate = _activeTemplate!.copyWith(layouts: updatedLayouts);
    await saveTemplate(updatedTemplate);
    _activeTemplate = updatedTemplate;
    _currentLayout = updatedLayout;
    notifyListeners();
  }
}
