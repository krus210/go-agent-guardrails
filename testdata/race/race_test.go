package race

import (
	"sync"
	"testing"
)

func TestRace(t *testing.T) {
	var value int
	var wg sync.WaitGroup
	for range 2 {
		wg.Go(func() { value++ })
	}
	wg.Wait()
	t.Log(value)
}
