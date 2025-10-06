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

    // Create default template if none exist
    if (_templates.isEmpty) {
      await _createDefaultTemplate();
    }

    _isLoading = false;
    notifyListeners();
  }

  /// Create a default template for first-time users
  Future<void> _createDefaultTemplate() async {
    final defaultTemplate = Template(
      name: 'Default WASD',
      layouts: {
        'fullscreen': LayoutConfig(
          minWidth: ScreenBreakpoints.fullscreenMinWidth,
          keys: [
            // WASD keys
            KeyConfig(
              label: 'W',
              keyCode: 'w',
              xPercent: 0.15,
              yPercent: 0.30,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            KeyConfig(
              label: 'A',
              keyCode: 'a',
              xPercent: 0.05,
              yPercent: 0.45,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            KeyConfig(
              label: 'S',
              keyCode: 's',
              xPercent: 0.15,
              yPercent: 0.45,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            KeyConfig(
              label: 'D',
              keyCode: 'd',
              xPercent: 0.25,
              yPercent: 0.45,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            // Space bar
            KeyConfig(
              label: 'SPACE',
              keyCode: 'space',
              xPercent: 0.10,
              yPercent: 0.65,
              widthPercent: 0.25,
              heightPercent: DefaultKeySizes.height,
            ),
            // Skills on right side
            KeyConfig(
              label: '1',
              keyCode: '1',
              xPercent: 0.70,
              yPercent: 0.30,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            KeyConfig(
              label: '2',
              keyCode: '2',
              xPercent: 0.70,
              yPercent: 0.45,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
            KeyConfig(
              label: 'E',
              keyCode: 'e',
              xPercent: 0.70,
              yPercent: 0.60,
              widthPercent: DefaultKeySizes.width,
              heightPercent: DefaultKeySizes.height,
            ),
          ],
        ),
      },
    );

    await saveTemplate(defaultTemplate);
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
