package download

import (
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

type trackedBody struct {
	io.Reader
	closeErr error
	closes   int
}

func (b *trackedBody) Close() error {
	b.closes++
	return b.closeErr
}

type errorReader struct{ err error }

func (r errorReader) Read([]byte) (int, error) {
	return 0, r.err
}

func TestDownloadReadAndClose(t *testing.T) {
	readFailure := errors.New("read failed")
	closeFailure := errors.New("close failed")
	const payload = "downloaded data"
	for _, tc := range []struct {
		name     string
		readErr  error
		closeErr error
	}{
		{name: "success"},
		{name: "read_error", readErr: readFailure},
		{name: "close_error", closeErr: closeFailure},
		{name: "read_and_close_errors", readErr: readFailure, closeErr: closeFailure},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reader := io.Reader(strings.NewReader(payload))
			if tc.readErr != nil {
				// Return some data before the read fails, so partial data is checked too.
				reader = io.MultiReader(reader, errorReader{err: tc.readErr})
			}
			body := &trackedBody{Reader: reader, closeErr: tc.closeErr}

			// Download uses http.Get. Keep these tests sequential while replacing
			// its default client, and restore that client after every subtest.
			original := http.DefaultClient
			t.Cleanup(func() { http.DefaultClient = original })
			http.DefaultClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode:    http.StatusOK,
					Header:        make(http.Header),
					Body:          body,
					Request:       r,
					ContentLength: -1,
				}, nil
			})}

			data, err := Download("http://download.test/resource")
			if string(data) != payload {
				t.Errorf("Download() data = %q, want %q", data, payload)
			}
			if body.closes != 1 {
				t.Errorf("Body.Close() calls = %d, want 1", body.closes)
			}
			if tc.readErr == nil && tc.closeErr == nil && err != nil {
				t.Errorf("Download() error = %v, want nil", err)
			}
			if errors.Is(err, readFailure) != (tc.readErr != nil) {
				t.Errorf("Download() error = %v, want read error present: %v", err, tc.readErr != nil)
			}
			if errors.Is(err, closeFailure) != (tc.closeErr != nil) {
				t.Errorf("Download() error = %v, want close error present: %v", err, tc.closeErr != nil)
			}
		})
	}
}
