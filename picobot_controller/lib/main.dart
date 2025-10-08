import 'dart:ui' as ui;
import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'services/storage_service.dart';
import 'providers/connection_provider.dart';
import 'providers/template_provider.dart';
import 'screens/home_screen.dart';
import 'services/lifecycle_reconnector.dart';
import 'services/logger_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Configure logger
  final logger = LoggerService()
    ..minLevel = kReleaseMode ? LogLevel.warn : LogLevel.debug
    ..bufferLimit = kReleaseMode ? 200 : 800
    ..enableConsole = !kReleaseMode;

  FlutterError.onError = (details) {
    logger.e('FlutterError', details.exceptionAsString(), details.exception, details.stack);
  };
  ui.PlatformDispatcher.instance.onError = (error, stack) {
    logger.e('Uncaught', '$error', error, stack);
    return true;
  };
  
  // Initialize storage service
  final storageService = StorageService();
  await storageService.init();
  
  runApp(PicoBotApp(storageService: storageService));
}

class PicoBotApp extends StatelessWidget {
  final StorageService storageService;
  
  const PicoBotApp({super.key, required this.storageService});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(
          create: (_) => ConnectionProvider(storageService),
        ),
        ChangeNotifierProvider(
          create: (_) => TemplateProvider(storageService),
        ),
      ],
      child: MaterialApp(
        title: 'PicoBot Controller',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(
            seedColor: Colors.blue,
            brightness: Brightness.dark,
          ),
          useMaterial3: true,
        ),
        home: const LifecycleReconnector(child: HomeScreen()),
      ),
    );
  }
}
