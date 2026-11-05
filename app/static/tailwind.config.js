tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        "on-primary": "#ffffff", "tertiary-fixed": "#bdece2", "secondary": "#006b5f",
        "surface-container-low": "#f2f4f6", "inverse-on-surface": "#eff1f3", "on-tertiary": "#ffffff",
        "primary-container": "#0f766e", "surface-container-lowest": "#ffffff", "on-tertiary-fixed": "#00201c",
        "surface-variant": "#e0e3e5", "on-error-container": "#93000a", "tertiary-container": "#467169",
        "on-primary-fixed": "#00201d", "on-surface": "#191c1e", "inverse-surface": "#2d3133",
        "background": "#f7f9fb", "on-tertiary-container": "#c5f3ea", "error": "#ba1a1a",
        "on-secondary-fixed-variant": "#005048", "on-secondary-container": "#006f64",
        "surface-container-high": "#e6e8ea", "on-primary-container": "#a3faef", "surface-tint": "#006a63",
        "on-secondary-fixed": "#00201c", "outline": "#6e7977", "surface-container-highest": "#e0e3e5",
        "error-container": "#ffdad6", "surface": "#f7f9fb", "primary-fixed": "#9cf2e8",
        "secondary-fixed-dim": "#4fdbc8", "surface-container": "#eceef0", "primary-fixed-dim": "#80d5cb",
        "on-tertiary-fixed-variant": "#224e47", "on-error": "#ffffff", "surface-dim": "#d8dadc",
        "inverse-primary": "#80d5cb", "primary": "#005c55", "on-primary-fixed-variant": "#00504a",
        "secondary-fixed": "#71f8e4", "on-secondary": "#ffffff", "outline-variant": "#bdc9c6",
        "tertiary-fixed-dim": "#a2d0c6", "tertiary": "#2d5951", "on-surface-variant": "#3e4947",
        "secondary-container": "#6df5e1", "on-background": "#191c1e", "surface-bright": "#f7f9fb"
      },
      borderRadius: { "DEFAULT": "0.25rem", "lg": "0.5rem", "xl": "0.75rem", "full": "9999px" },
      spacing: {
        "gutter-desktop": "1.5rem", "space-2xl": "3rem", "margin-tablet": "1.5rem", "margin-desktop": "2.5rem",
        "space-md": "1rem", "margin": "1rem", "space-xs": "0.25rem", "gutter": "1rem", "space-sm": "0.5rem",
        "space-xl": "2rem", "space-lg": "1.5rem"
      },
      fontFamily: {
        "body-lg": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "body-md": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "body-sm": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "label-lg": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "label-md": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "label-sm": ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        "headline-xl": ["Nunito Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        "headline-lg": ["Nunito Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        "headline-md": ["Nunito Sans", "ui-sans-serif", "system-ui", "sans-serif"]
      },
      fontSize: {
        "body-lg": ["18px", { "lineHeight": "28px", "letterSpacing": "0", "fontWeight": "400" }],
        "headline-xl": ["36px", { "lineHeight": "44px", "letterSpacing": "-0.02em", "fontWeight": "800" }],
        "label-lg": ["16px", { "lineHeight": "24px", "letterSpacing": "0.01em", "fontWeight": "600" }],
        "label-md": ["14px", { "lineHeight": "20px", "letterSpacing": "0.02em", "fontWeight": "600" }],
        "headline-md": ["20px", { "lineHeight": "28px", "letterSpacing": "0", "fontWeight": "700" }],
        "label-sm": ["12px", { "lineHeight": "16px", "letterSpacing": "0.03em", "fontWeight": "600" }],
        "headline-lg": ["28px", { "lineHeight": "36px", "letterSpacing": "-0.01em", "fontWeight": "700" }],
        "body-md": ["16px", { "lineHeight": "24px", "letterSpacing": "0", "fontWeight": "400" }],
        "body-sm": ["14px", { "lineHeight": "20px", "letterSpacing": "0.01em", "fontWeight": "400" }]
      }
    }
  }
};
