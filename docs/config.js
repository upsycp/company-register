// Public settings for the website. Both values are meant to be public: the data is protected by
// Supabase sign-in and row-level security (only emails in the "team" table can read anything).
// Fill these in from Supabase → Project Settings → API.
window.RADAR_CONFIG = {
  supabaseUrl: "",       // e.g. "https://abcdefgh.supabase.co"
  supabaseAnonKey: "",   // the "anon public" key
};
