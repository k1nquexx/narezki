/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          950: "#09090d",
          900: "#101018",
          850: "#161621",
          800: "#1d1d2b",
          700: "#28283a",
        },
        accent: {
          500: "#8b5cf6",
          400: "#a78bfa",
          600: "#7c3aed",
        },
      },
    },
  },
  plugins: [],
};
