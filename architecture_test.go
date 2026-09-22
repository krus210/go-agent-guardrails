package guardrails_test

import (
	"slices"
	"strings"
	"testing"

	"golang.org/x/tools/go/packages"
)

const (
	modulePath      = "example.com/guardrails"
	calculationRoot = modulePath + "/internal/reportcalc"
	storageRoot     = modulePath + "/internal/storage"
)

func inside(path, root string) bool {
	return path == root || strings.HasPrefix(path, root+"/")
}

func forbiddenPath(pkg *packages.Package, root string, seen map[string]bool) []string {
	if seen[pkg.PkgPath] {
		return nil
	}
	seen[pkg.PkgPath] = true
	if inside(pkg.PkgPath, root) {
		return []string{pkg.PkgPath}
	}
	imports := make([]string, 0, len(pkg.Imports))
	for path := range pkg.Imports {
		imports = append(imports, path)
	}
	slices.Sort(imports) // Stable diagnostics if multiple paths exist.
	for _, path := range imports {
		if chain := forbiddenPath(pkg.Imports[path], root, seen); chain != nil {
			return append([]string{pkg.PkgPath}, chain...)
		}
	}
	return nil
}

func TestArchitecture(t *testing.T) {
	pkgs, err := packages.Load(&packages.Config{
		Mode:  packages.NeedName | packages.NeedImports | packages.NeedDeps | packages.NeedTypes,
		Tests: false,
	}, "./internal/...")
	if err != nil {
		t.Fatal(err)
	}
	if len(pkgs) == 0 || packages.PrintErrors(pkgs) > 0 {
		t.Fatal("cannot load the production package graph")
	}
	targetFound := false
	for _, pkg := range pkgs {
		if pkg.PkgPath == storageRoot {
			targetFound = true
		}
	}
	if !targetFound {
		t.Fatal("architecture target package missing; review storageRoot")
	}
	checked := 0
	for _, pkg := range pkgs {
		if !inside(pkg.PkgPath, calculationRoot) {
			continue
		}
		checked++
		if chain := forbiddenPath(pkg, storageRoot, make(map[string]bool)); chain != nil {
			t.Errorf("ARCH001: calculation depends on storage: %s", strings.Join(chain, " -> "))
		}
	}
	if checked == 0 {
		t.Fatal("no calculation packages checked; review the architecture rule")
	}
}

func TestForbiddenPath(t *testing.T) {
	const start = "sample/calc"
	const middle = "sample/helper"
	const target = "sample/store"
	store := &packages.Package{PkgPath: target}
	helper := &packages.Package{PkgPath: middle, Imports: map[string]*packages.Package{target: store}}
	for _, tc := range []struct {
		name    string
		imports map[string]*packages.Package
		want    []string
	}{
		{name: "allowed", imports: nil, want: nil},
		{name: "direct", imports: map[string]*packages.Package{target: store}, want: []string{start, target}},
		{name: "transitive", imports: map[string]*packages.Package{middle: helper}, want: []string{start, middle, target}},
		{name: "same prefix", imports: map[string]*packages.Package{target + "house": {PkgPath: target + "house"}}, want: nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pkg := &packages.Package{PkgPath: start, Imports: tc.imports}
			if got := forbiddenPath(pkg, target, make(map[string]bool)); !slices.Equal(got, tc.want) {
				t.Fatalf("path = %v, want %v", got, tc.want)
			}
		})
	}
}
