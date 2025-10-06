// Basic widget tests for PicoBot Controller

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:picobot_controller/services/storage_service.dart';
import 'package:picobot_controller/main.dart';

void main() {
  testWidgets('App loads and shows home screen', (WidgetTester tester) async {
    // Initialize storage service
    final storageService = StorageService();
    await storageService.init();

    // Build our app and trigger a frame
    await tester.pumpWidget(PicoBotApp(storageService: storageService));
    await tester.pumpAndSettle();

    // Verify that the home screen loads
    expect(find.text('PicoBot Controller'), findsOneWidget);
  });

  testWidgets('Can navigate to settings', (WidgetTester tester) async {
    // Initialize storage service
    final storageService = StorageService();
    await storageService.init();

    // Build our app
    await tester.pumpWidget(PicoBotApp(storageService: storageService));
    await tester.pumpAndSettle();

    // Tap settings icon
    await tester.tap(find.byIcon(Icons.settings));
    await tester.pumpAndSettle();

    // Verify settings screen appears
    expect(find.text('Settings'), findsOneWidget);
    expect(find.text('Server Configuration'), findsOneWidget);
  });
}
