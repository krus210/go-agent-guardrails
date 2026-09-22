package download

import (
	"errors"
	"io"
	"net/http"
)

func Download(url string) (data []byte, err error) {
	response, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer func() {
		err = errors.Join(err, response.Body.Close())
	}()
	return io.ReadAll(response.Body)
}
