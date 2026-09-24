# shell.nix
{ pkgs ? import <nixpkgs> {} }:
let
  python-with-my-packages = pkgs.python3.withPackages (p: with p; [
    requests # retained for the historical v1 scripts
    beautifulsoup4
    # other python packages you want
  ]);
in
python-with-my-packages.env # replacement for pkgs.mkShell
