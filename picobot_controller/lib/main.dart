import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'services/storage_service.dart';
import 'providers/connection_provider.dart';
import 'providers/template_provider.dart';
import 'screens/home_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
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
        home: const HomeScreen(),
      ),
    );
  }
}
