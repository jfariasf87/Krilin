package dev.krilin.bridge

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Provisioned locally via ADB. This development APK is not a public service.
        val token = intent.getStringExtra("token")
        if (token != null && token.matches(Regex("[a-f0-9]{64}"))) {
            getSharedPreferences("bridge", MODE_PRIVATE).edit().putString("token", token).apply()
            finish()
            return
        }
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 64, 32, 32)
        }
        layout.addView(TextView(this).apply {
            setText(R.string.setup_instructions)
        })
        layout.addView(Button(this).apply {
            setText(R.string.accessibility_settings)
            setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        })
        layout.addView(Button(this).apply {
            setText(R.string.open_test_screen)
            setOnClickListener { startActivity(Intent(this@MainActivity, DemoActivity::class.java)) }
        })
        setContentView(layout)
    }
}
